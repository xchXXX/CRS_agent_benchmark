"""In-memory index for parameter-query lookups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import threading
from typing import Any

from app.agent.domain.parameter_query.models import AliasEntry, ParameterIndexRow, ParameterIndexSource
from app.legacy.models.database import ParamAlias, ParamKnowledgeSource, ParamPinRow


@dataclass(frozen=True)
class ParameterKnowledgeIndex:
    built_at: datetime
    sources_by_id: dict[int, ParameterIndexSource]
    rows_by_id: dict[int, ParameterIndexRow]
    rows_by_source: dict[int, tuple[ParameterIndexRow, ...]]
    rows_by_pin: dict[str, tuple[ParameterIndexRow, ...]]
    rows_by_ecu: dict[str, tuple[ParameterIndexRow, ...]]
    alias_lookup: dict[str, dict[str, tuple[AliasEntry, ...]]]

    @property
    def source_count(self) -> int:
        return len(self.sources_by_id)

    @property
    def row_count(self) -> int:
        return len(self.rows_by_id)


class ParameterQueryIndexStore:
    """Thread-safe container for the in-memory parameter index."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._index: ParameterKnowledgeIndex | None = None

    def get(self) -> ParameterKnowledgeIndex | None:
        with self._lock:
            return self._index

    def has_data(self) -> bool:
        snapshot = self.get()
        return bool(snapshot and snapshot.row_count > 0)

    def replace(self, index: ParameterKnowledgeIndex) -> None:
        with self._lock:
            self._index = index

    def rebuild(self, session_factory: Any) -> ParameterKnowledgeIndex:
        session = session_factory()
        try:
            source_rows = (
                session.query(ParamKnowledgeSource)
                .filter(
                    ParamKnowledgeSource.parse_status == "ready",
                    ParamKnowledgeSource.source_status.is_(True),
                    ParamKnowledgeSource.source_is_deleted.is_(False),
                )
                .all()
            )
            pin_rows = session.query(ParamPinRow).all()
            alias_rows = (
                session.query(ParamAlias)
                .filter(ParamAlias.is_active.is_(True))
                .order_by(ParamAlias.priority.desc(), ParamAlias.id.asc())
                .all()
            )
        finally:
            session.close()

        sources_by_id: dict[int, ParameterIndexSource] = {}
        for item in source_rows:
            sources_by_id[int(item.source_knowledge_id)] = ParameterIndexSource(
                source_knowledge_id=int(item.source_knowledge_id),
                title=str(item.title or ""),
                title_normalized=str(item.title_normalized or ""),
                ecu_name=item.ecu_name,
                ecu_name_normalized=item.ecu_name_normalized,
                system_voltage=int(item.system_voltage) if item.system_voltage is not None else None,
                pin_doc_kind=str(item.pin_doc_kind or "unknown"),
                parsed_row_count=int(item.parsed_row_count or 0),
                raw_content=item.raw_content,
                last_synced_at=item.last_synced_at,
            )

        rows_by_id: dict[int, ParameterIndexRow] = {}
        rows_by_source_mutable: dict[int, list[ParameterIndexRow]] = {}
        rows_by_pin_mutable: dict[str, list[ParameterIndexRow]] = {}
        rows_by_ecu_mutable: dict[str, list[ParameterIndexRow]] = {}
        for item in pin_rows:
            if int(item.source_knowledge_id) not in sources_by_id:
                continue
            row = ParameterIndexRow(
                id=int(item.id),
                source_knowledge_id=int(item.source_knowledge_id),
                source_title=str(item.source_title or ""),
                ecu_name=item.ecu_name,
                ecu_name_normalized=item.ecu_name_normalized,
                system_voltage=int(item.system_voltage) if item.system_voltage is not None else None,
                row_no=int(item.row_no),
                component_name=item.component_name,
                component_name_normalized=item.component_name_normalized,
                ecu_pin_no=item.ecu_pin_no,
                ecu_pin_no_normalized=item.ecu_pin_no_normalized,
                pin_definition=item.pin_definition,
                pin_definition_normalized=item.pin_definition_normalized,
                connector_pin_no=item.connector_pin_no,
                open_voltage_text=item.open_voltage_text,
                static_voltage_text=item.static_voltage_text,
                idle_voltage_text=item.idle_voltage_text,
                remark=item.remark,
                raw_row_json=dict(item.raw_row_json or {}) if item.raw_row_json else None,
                search_text=item.search_text,
            )
            rows_by_id[row.id] = row
            rows_by_source_mutable.setdefault(row.source_knowledge_id, []).append(row)
            if row.ecu_pin_no_normalized:
                rows_by_pin_mutable.setdefault(row.ecu_pin_no_normalized, []).append(row)
            if row.ecu_name_normalized:
                rows_by_ecu_mutable.setdefault(row.ecu_name_normalized, []).append(row)

        alias_lookup_mutable: dict[str, dict[str, list[AliasEntry]]] = {}
        for item in alias_rows:
            entity_aliases = alias_lookup_mutable.setdefault(str(item.entity_type), {})
            alias_value = str(item.alias_value_normalized or "")
            if not alias_value:
                continue
            entity_aliases.setdefault(alias_value, []).append(
                AliasEntry(
                    entity_type=str(item.entity_type),
                    canonical_value=str(item.canonical_value),
                    canonical_value_normalized=str(item.canonical_value_normalized),
                    alias_value=str(item.alias_value),
                    alias_value_normalized=alias_value,
                    priority=int(item.priority or 0),
                    source_scope=str(item.source_scope or "system"),
                    source_knowledge_id=int(item.source_knowledge_id) if item.source_knowledge_id else None,
                )
            )

        index = ParameterKnowledgeIndex(
            built_at=datetime.utcnow(),
            sources_by_id=sources_by_id,
            rows_by_id=rows_by_id,
            rows_by_source={key: tuple(value) for key, value in rows_by_source_mutable.items()},
            rows_by_pin={key: tuple(value) for key, value in rows_by_pin_mutable.items()},
            rows_by_ecu={key: tuple(value) for key, value in rows_by_ecu_mutable.items()},
            alias_lookup={
                entity_type: {alias: tuple(entries) for alias, entries in aliases.items()}
                for entity_type, aliases in alias_lookup_mutable.items()
            },
        )
        self.replace(index)
        return index

