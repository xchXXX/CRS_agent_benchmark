"""Synchronization service for parameter-query knowledge."""

from __future__ import annotations

from datetime import datetime
import hashlib
import logging
import re
from typing import Any

from sqlalchemy import delete

from app.agent.domain.parameter_query.external_repository import ExternalParameterKnowledgeRepository
from app.agent.domain.parameter_query.index_store import ParameterQueryIndexStore
from app.agent.domain.parameter_query.models import FIELD_ALIASES, ExternalKnowledgeSource
from app.agent.domain.parameter_query.normalizer import detect_system_voltage, normalize_pin_no, normalize_text
from app.agent.domain.parameter_query.parser import parse_markdown_pin_rows
from app.core.config import settings
from app.legacy.models.database import (
    Base,
    ParamAlias,
    ParamKnowledgeSource,
    ParamPinRow,
    ParamSyncJob,
    get_engine,
)


logger = logging.getLogger(__name__)


TITLE_KIND_PATTERNS = (
    ("pin_voltage", re.compile(r"针脚电压", re.IGNORECASE)),
    ("pin_definition", re.compile(r"针脚定义", re.IGNORECASE)),
)


class ParameterKnowledgeSyncService:
    """Sync external pin_info knowledge into the local structured projection."""

    def __init__(
        self,
        *,
        session_factory: Any,
        external_repository: ExternalParameterKnowledgeRepository,
        index_store: ParameterQueryIndexStore,
        config_service: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._external_repository = external_repository
        self._index_store = index_store
        self._config_service = config_service

    def ensure_local_schema(self) -> None:
        engine = get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[
                ParamKnowledgeSource.__table__,
                ParamPinRow.__table__,
                ParamAlias.__table__,
                ParamSyncJob.__table__,
            ],
            checkfirst=True,
        )

    def sync(self, *, job_type: str = "startup_sync") -> dict[str, Any]:
        self.ensure_local_schema()
        started_at = datetime.utcnow()
        job = ParamSyncJob(
            job_type=job_type,
            sync_scope="pin_info",
            parser_version=self._parser_version,
            status="running",
            started_at=started_at,
        )
        session = self._session_factory()
        result_payload: dict[str, Any] = {}
        try:
            session.add(job)
            session.commit()
            session.refresh(job)

            external_sources = self._external_repository.list_pin_info_sources()
            job.total_source_count = len(external_sources)
            local_sources = {
                int(item.source_knowledge_id): item
                for item in session.query(ParamKnowledgeSource).all()
            }

            new_count = 0
            updated_count = 0
            disabled_count = 0
            failed_count = 0
            changed_sources: list[ExternalKnowledgeSource] = []
            disabled_sources: list[ExternalKnowledgeSource] = []
            for external in external_sources:
                local = local_sources.get(external.source_knowledge_id)
                if not external.source_status or external.source_is_deleted:
                    disabled_sources.append(external)
                    continue
                if local is None:
                    changed_sources.append(external)
                    new_count += 1
                    continue
                if self._needs_resync(local, external):
                    changed_sources.append(external)
                    updated_count += 1

            if disabled_sources:
                for external in disabled_sources:
                    self._mark_source_disabled(session, external)
                disabled_count = len(disabled_sources)
                session.commit()

            if changed_sources:
                contents = self._external_repository.fetch_contents(
                    [item.source_knowledge_id for item in changed_sources]
                )
                for external in changed_sources:
                    try:
                        self._sync_single_source(
                            session=session,
                            external=external,
                            content=contents.get(external.source_knowledge_id, ""),
                        )
                        session.commit()
                    except Exception as exc:
                        session.rollback()
                        failed_count += 1
                        logger.exception(
                            "parameter query source sync failed. source_id=%s title=%s reason=%s",
                            external.source_knowledge_id,
                            external.title,
                            exc,
                        )
                        self._mark_source_failed(session, external, str(exc))
                        session.commit()

            self._rebuild_aliases(session)
            session.commit()

            job.status = "success" if failed_count == 0 else "partial_failed"
            job.new_source_count = new_count
            job.updated_source_count = updated_count
            job.disabled_source_count = disabled_count
            job.failed_source_count = failed_count
            job.finished_at = datetime.utcnow()
            session.commit()
            result_payload = {
                "job_id": int(job.id),
                "status": job.status,
                "source_count": len(external_sources),
            }
        except Exception as exc:
            session.rollback()
            logger.exception("parameter knowledge sync failed: %s", exc)
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = datetime.utcnow()
            session.add(job)
            session.commit()
            raise
        finally:
            session.close()

        index = self._index_store.rebuild(self._session_factory)
        result_payload["ready_source_count"] = index.source_count
        result_payload["ready_row_count"] = index.row_count
        return result_payload

    def _needs_resync(self, local: ParamKnowledgeSource, external: ExternalKnowledgeSource) -> bool:
        if str(local.parser_version or "") != self._parser_version:
            return True
        if str(local.parse_status or "") != "ready":
            return True
        if int(local.source_latest_version or 0) != int(external.latest_version or 0):
            return True
        if local.source_update_time != external.update_time:
            return True
        return False

    def _sync_single_source(
        self,
        *,
        session: Any,
        external: ExternalKnowledgeSource,
        content: str,
    ) -> None:
        parsed_rows = parse_markdown_pin_rows(content)
        source = (
            session.query(ParamKnowledgeSource)
            .filter(ParamKnowledgeSource.source_knowledge_id == external.source_knowledge_id)
            .one_or_none()
        )
        if source is None:
            source = ParamKnowledgeSource(source_knowledge_id=external.source_knowledge_id)
            session.add(source)

        ecu_name = self._extract_ecu_name(external.title)
        ecu_name_normalized = normalize_text(ecu_name)
        content_hash = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
        pin_doc_kind = self._detect_doc_kind(external.title)

        source.source_type_id = external.source_type_id
        source.source_type_code = external.source_type_code
        source.title = external.title
        source.title_normalized = normalize_text(external.title)
        source.ecu_name = ecu_name
        source.ecu_name_normalized = ecu_name_normalized or None
        source.system_voltage = detect_system_voltage(external.title)
        source.pin_doc_kind = pin_doc_kind
        source.content_format = external.content_format or "text"
        source.content_summary = external.content_summary
        source.raw_content = content
        source.content_hash = content_hash
        source.source_status = external.source_status
        source.source_is_deleted = external.source_is_deleted
        source.source_latest_version = external.latest_version
        source.source_published_version = external.published_version
        source.source_update_time = external.update_time
        source.parser_version = self._parser_version
        source.parse_status = "ready"
        source.parse_error = None
        source.parsed_row_count = len(parsed_rows)
        source.last_synced_at = datetime.utcnow()

        session.flush()
        session.execute(
            delete(ParamPinRow).where(ParamPinRow.source_knowledge_id == external.source_knowledge_id)
        )
        for row in parsed_rows:
            component_name_normalized = normalize_text(row.component_name)
            pin_definition_normalized = normalize_text(row.pin_definition)
            ecu_pin_no_normalized = normalize_pin_no(row.ecu_pin_no)
            search_text = " ".join(
                item
                for item in [
                    source.title,
                    source.ecu_name or "",
                    row.component_name or "",
                    row.ecu_pin_no or "",
                    row.pin_definition or "",
                    row.connector_pin_no or "",
                    row.open_voltage_text or "",
                    row.static_voltage_text or "",
                    row.idle_voltage_text or "",
                    row.remark or "",
                ]
                if item
            )
            session.add(
                ParamPinRow(
                    source_knowledge_id=external.source_knowledge_id,
                    source_title=external.title,
                    ecu_name=source.ecu_name,
                    ecu_name_normalized=source.ecu_name_normalized,
                    system_voltage=source.system_voltage,
                    row_no=row.row_no,
                    component_name=row.component_name,
                    component_name_normalized=component_name_normalized or None,
                    ecu_pin_no=row.ecu_pin_no,
                    ecu_pin_no_normalized=ecu_pin_no_normalized,
                    pin_definition=row.pin_definition,
                    pin_definition_normalized=pin_definition_normalized or None,
                    connector_pin_no=row.connector_pin_no,
                    open_voltage_text=row.open_voltage_text,
                    open_voltage_min=row.open_voltage_min,
                    open_voltage_max=row.open_voltage_max,
                    static_voltage_text=row.static_voltage_text,
                    static_voltage_min=row.static_voltage_min,
                    static_voltage_max=row.static_voltage_max,
                    idle_voltage_text=row.idle_voltage_text,
                    idle_voltage_min=row.idle_voltage_min,
                    idle_voltage_max=row.idle_voltage_max,
                    remark=row.remark,
                    raw_row_json=row.raw_row,
                    search_text=search_text,
                )
            )

    def _mark_source_disabled(self, session: Any, external: ExternalKnowledgeSource) -> None:
        source = (
            session.query(ParamKnowledgeSource)
            .filter(ParamKnowledgeSource.source_knowledge_id == external.source_knowledge_id)
            .one_or_none()
        )
        if source is None:
            return
        source.source_status = external.source_status
        source.source_is_deleted = external.source_is_deleted
        source.parse_status = "skipped"
        source.parsed_row_count = 0
        source.last_synced_at = datetime.utcnow()
        session.execute(delete(ParamPinRow).where(ParamPinRow.source_knowledge_id == external.source_knowledge_id))

    def _mark_source_failed(self, session: Any, external: ExternalKnowledgeSource, reason: str) -> None:
        source = (
            session.query(ParamKnowledgeSource)
            .filter(ParamKnowledgeSource.source_knowledge_id == external.source_knowledge_id)
            .one_or_none()
        )
        if source is None:
            source = ParamKnowledgeSource(source_knowledge_id=external.source_knowledge_id)
            session.add(source)
        source.source_type_id = external.source_type_id
        source.source_type_code = external.source_type_code
        source.title = external.title
        source.title_normalized = normalize_text(external.title)
        source.source_status = external.source_status
        source.source_is_deleted = external.source_is_deleted
        source.source_latest_version = external.latest_version
        source.source_published_version = external.published_version
        source.source_update_time = external.update_time
        source.parser_version = self._parser_version
        source.parse_status = "failed"
        source.parse_error = reason[:1000]
        source.last_synced_at = datetime.utcnow()

    @property
    def _parser_version(self) -> str:
        if self._config_service is None:
            return settings.param_query_parser_version
        value = self._config_service.get("param_query_parser_version", settings.param_query_parser_version)
        return str(value or settings.param_query_parser_version)

    def _rebuild_aliases(self, session: Any) -> None:
        session.execute(
            delete(ParamAlias).where(ParamAlias.source_scope.in_(("system", "generated")))
        )
        seen_aliases: set[tuple[str, str, str]] = set()
        for field, aliases in FIELD_ALIASES.items():
            canonical_value = field
            canonical_normalized = normalize_text(field)
            self._add_alias(
                session=session,
                entity_type="field",
                canonical_value=canonical_value,
                alias_value=field,
                source_scope="system",
                priority=200,
                source_knowledge_id=None,
                seen_aliases=seen_aliases,
            )
            for alias in aliases:
                self._add_alias(
                    session=session,
                    entity_type="field",
                    canonical_value=canonical_value,
                    alias_value=alias,
                    source_scope="system",
                    priority=220,
                    source_knowledge_id=None,
                    seen_aliases=seen_aliases,
                )

        sources = (
            session.query(ParamKnowledgeSource)
            .filter(
                ParamKnowledgeSource.parse_status == "ready",
                ParamKnowledgeSource.source_status.is_(True),
                ParamKnowledgeSource.source_is_deleted.is_(False),
            )
            .all()
        )
        for source in sources:
            if not source.ecu_name:
                continue
            self._add_alias(
                session=session,
                entity_type="ecu",
                canonical_value=source.ecu_name,
                alias_value=source.ecu_name,
                source_scope="generated",
                priority=180,
                source_knowledge_id=int(source.source_knowledge_id),
                seen_aliases=seen_aliases,
            )
            simplified_title = self._extract_ecu_name(source.title)
            if simplified_title and simplified_title != source.ecu_name:
                self._add_alias(
                    session=session,
                    entity_type="ecu",
                    canonical_value=source.ecu_name,
                    alias_value=simplified_title,
                    source_scope="generated",
                    priority=150,
                    source_knowledge_id=int(source.source_knowledge_id),
                    seen_aliases=seen_aliases,
                )
            self._add_alias(
                session=session,
                entity_type="ecu",
                canonical_value=source.ecu_name,
                alias_value=source.title,
                source_scope="generated",
                priority=120,
                source_knowledge_id=int(source.source_knowledge_id),
                seen_aliases=seen_aliases,
            )
            for core_alias in self._extract_ecu_core_aliases(source.title, source.ecu_name):
                self._add_alias(
                    session=session,
                    entity_type="ecu",
                    canonical_value=source.ecu_name,
                    alias_value=core_alias,
                    source_scope="generated",
                    priority=165,
                    source_knowledge_id=int(source.source_knowledge_id),
                    seen_aliases=seen_aliases,
                )

        rows = session.query(ParamPinRow).all()
        for row in rows:
            if row.component_name:
                self._add_alias(
                    session=session,
                    entity_type="component",
                    canonical_value=row.component_name,
                    alias_value=row.component_name,
                    source_scope="generated",
                    priority=130,
                    source_knowledge_id=int(row.source_knowledge_id),
                    seen_aliases=seen_aliases,
                )
            if row.pin_definition:
                self._add_alias(
                    session=session,
                    entity_type="component",
                    canonical_value=row.pin_definition,
                    alias_value=row.pin_definition,
                    source_scope="generated",
                    priority=90,
                    source_knowledge_id=int(row.source_knowledge_id),
                    seen_aliases=seen_aliases,
                )

    def _add_alias(
        self,
        *,
        session: Any,
        entity_type: str,
        canonical_value: str,
        alias_value: str,
        source_scope: str,
        priority: int,
        source_knowledge_id: int | None,
        seen_aliases: set[tuple[str, str, str]],
    ) -> None:
        canonical_normalized = normalize_text(canonical_value)
        alias_normalized = normalize_text(alias_value)
        if not canonical_normalized or not alias_normalized:
            return
        unique_key = (entity_type, canonical_normalized, alias_normalized)
        if unique_key in seen_aliases:
            return
        seen_aliases.add(unique_key)
        session.add(
            ParamAlias(
                entity_type=entity_type,
                canonical_value=canonical_value,
                canonical_value_normalized=canonical_normalized,
                alias_value=alias_value,
                alias_value_normalized=alias_normalized,
                source_scope=source_scope,
                source_knowledge_id=source_knowledge_id,
                priority=priority,
                is_active=True,
            )
        )

    @staticmethod
    def _extract_ecu_name(title: str) -> str:
        stripped = re.sub(r"针脚(?:电压|定义|信息).*$", "", title, flags=re.IGNORECASE)
        return stripped.strip(" -_[]()（）【】")

    @staticmethod
    def _detect_doc_kind(title: str) -> str:
        for kind, pattern in TITLE_KIND_PATTERNS:
            if pattern.search(title):
                return kind
        return "unknown"

    @staticmethod
    def _extract_ecu_core_aliases(*values: str | None) -> list[str]:
        aliases: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value:
                continue
            for token in re.findall(r"[A-Za-z]+[A-Za-z0-9.]*\d+[A-Za-z0-9.]*", value):
                normalized = normalize_text(token)
                if len(normalized) < 3 or normalized in seen:
                    continue
                seen.add(normalized)
                aliases.append(token)
        return aliases
