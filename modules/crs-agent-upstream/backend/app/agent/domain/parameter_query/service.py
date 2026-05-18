"""Domain service for structured parameter queries."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

from app.agent.domain.parameter_query.external_repository import ExternalParameterKnowledgeRepository
from app.agent.domain.parameter_query.index_store import ParameterKnowledgeIndex, ParameterQueryIndexStore
from app.agent.domain.parameter_query.llm_normalizer import (
    ParameterQueryIntent,
    ParameterQueryRowCandidate,
    ParameterQueryRowSelection,
    ParameterQuerySourceCandidate,
    PydanticAIParameterQueryNormalizer,
)
from app.agent.domain.parameter_query.models import FIELD_LABELS, ParameterIndexRow, ParameterIndexSource
from app.agent.domain.parameter_query.normalizer import (
    extract_requested_field,
    normalize_free_text_hint,
    normalize_pin_no,
    normalize_text,
    text_similarity,
)
from app.agent.domain.parameter_query.sync_service import ParameterKnowledgeSyncService
from app.agent.models.tool_result import (
    ClarifyCandidate,
    ClarifyCandidateOption,
    SelectionPayload,
    ToolResultEnvelope,
    ToolResultStatus,
)
from app.core.config import settings
from app.legacy.models.database import ParamKnowledgeSource


@dataclass(frozen=True)
class SourceResolution:
    state: Literal["exact_match", "need_clarify", "ecu_not_found"]
    source: ParameterIndexSource | None = None
    candidate_source_ids: tuple[int, ...] = ()
    message: str | None = None


@dataclass(frozen=True)
class RowResolution:
    state: Literal["exact_match", "need_clarify", "missing_target", "pin_not_found"]
    rows: tuple[ParameterIndexRow, ...] = ()
    candidate_rows: tuple[ParameterIndexRow, ...] = ()
    message: str | None = None


class ParameterQueryService:
    """Stable entrypoint for parameter-query sync and structured lookup."""

    def __init__(
        self,
        *,
        session_factory: Any,
        external_repository: ExternalParameterKnowledgeRepository | None = None,
        index_store: ParameterQueryIndexStore | None = None,
        llm_normalizer: PydanticAIParameterQueryNormalizer | Any | None = None,
        config_service: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config_service = config_service
        self._index_store = index_store or ParameterQueryIndexStore()
        self._sync_service = ParameterKnowledgeSyncService(
            session_factory=session_factory,
            external_repository=external_repository or ExternalParameterKnowledgeRepository(),
            index_store=self._index_store,
            config_service=config_service,
        )
        self._llm_normalizer = llm_normalizer or PydanticAIParameterQueryNormalizer(
            config_service=config_service,
        )

    @property
    def index_store(self) -> ParameterQueryIndexStore:
        return self._index_store

    def ensure_local_index(self) -> dict[str, int]:
        self._sync_service.ensure_local_schema()
        index = self._index_store.rebuild(self._session_factory)
        return {"source_count": index.source_count, "row_count": index.row_count}

    def sync_now(self, *, job_type: str = "manual_sync") -> dict[str, Any]:
        return self._sync_service.sync(job_type=job_type)

    def get_source_detail(self, source_id: int | str) -> dict[str, Any] | None:
        session = self._session_factory()
        try:
            source = (
                session.query(ParamKnowledgeSource)
                .filter(ParamKnowledgeSource.source_knowledge_id == int(source_id))
                .one_or_none()
            )
        finally:
            session.close()
        if source is None:
            return None
        return {
            "id": str(source.source_knowledge_id),
            "title": source.title,
            "ecu_name": source.ecu_name,
            "system_voltage": int(source.system_voltage) if source.system_voltage is not None else None,
            "content": source.raw_content or "",
        }

    def query(
        self,
        query: str,
        selection_payload: dict[str, Any] | None = None,
        raw_query: str | None = None,
    ) -> dict[str, Any]:
        if not self._get_bool_config("param_query_enabled", settings.param_query_enabled):
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={"message": "参数查询能力未启用。"},
            ).model_dump(mode="json")

        index = self._index_store.get()
        if index is None:
            self.ensure_local_index()
            index = self._index_store.get()
        if index is None or index.row_count <= 0:
            return self._build_no_match_envelope(
                query=query,
                reason="local_parameter_cache_empty",
                message="参数资料库当前暂无可用数据，请稍后再试。",
            )

        filters = self._selection_filters(selection_payload)
        selected_source_id = self._safe_int(filters.get("param_source_id"))
        selected_row_id = self._safe_int(filters.get("param_row_id"))
        forced_field = str(filters.get("param_field") or "").strip() or None

        selected_source = index.sources_by_id.get(selected_source_id) if selected_source_id is not None else None
        source_catalog = self._build_source_catalog(index)
        intent = self._llm_normalizer.interpret_query(
            query=query,
            candidate_sources=source_catalog,
            selected_source_id=selected_source_id,
            selected_source_title=selected_source.title if selected_source else None,
        )
        requested_field = forced_field or intent.requested_field or self._default_requested_field(
            query=query,
            target_type=intent.target_type,
        )
        intent = intent.model_copy(update={"requested_field": requested_field})

        source_resolution = self._resolve_source(
            query=query,
            raw_query=raw_query,
            index=index,
            intent=intent,
            selected_source_id=selected_source_id,
        )
        if source_resolution.state == "need_clarify":
            return self._build_source_clarify_envelope(query, index, source_resolution, intent)
        if source_resolution.state == "ecu_not_found" or source_resolution.source is None:
            return self._build_source_clarify_envelope(
                query,
                index,
                source_resolution,
                intent,
                reason="ecu_not_found",
                message=self._append_ecu_confirm_hint(
                    source_resolution.message or "本地参数资料库中暂无相关 ECU 资料。"
                ),
            )

        source = source_resolution.source
        source_rows = list(index.rows_by_source.get(source.source_knowledge_id, ()))
        row_resolution = self._resolve_rows(
            query=query,
            source=source,
            rows=source_rows,
            intent=intent,
            selected_row_id=selected_row_id,
        )
        if row_resolution.state == "need_clarify":
            return self._build_row_clarify_envelope(query, source, row_resolution.candidate_rows, intent)
        if row_resolution.state == "missing_target":
            return self._build_row_clarify_envelope(
                query,
                source,
                row_resolution.candidate_rows,
                intent,
                reason="missing_target",
                message=row_resolution.message,
                example_rows=tuple(source_rows),
            )
        if row_resolution.state == "pin_not_found":
            return self._build_no_match_envelope(
                query=query,
                reason="pin_not_found_under_ecu",
                message=row_resolution.message
                or (
                    f"已定位到 ECU 资料《{source.title}》，但没有找到对应的针脚信息。"
                    "请检查针脚输入是否正确后重新发送。"
                ),
                selected_source=source,
            )

        return ToolResultEnvelope(
            status=ToolResultStatus.OK,
            data=self._build_match_payload(query, source, list(row_resolution.rows), requested_field),
        ).model_dump(mode="json")

    async def query_async(
        self,
        query: str,
        selection_payload: dict[str, Any] | None = None,
        raw_query: str | None = None,
    ) -> dict[str, Any]:
        if not self._get_bool_config("param_query_enabled", settings.param_query_enabled):
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={"message": "参数查询能力未启用。"},
            ).model_dump(mode="json")

        index = self._index_store.get()
        if index is None:
            self.ensure_local_index()
            index = self._index_store.get()
        if index is None or index.row_count <= 0:
            return self._build_no_match_envelope(
                query=query,
                reason="local_parameter_cache_empty",
                message="参数资料库当前暂无可用数据，请稍后再试。",
            )

        filters = self._selection_filters(selection_payload)
        selected_source_id = self._safe_int(filters.get("param_source_id"))
        selected_row_id = self._safe_int(filters.get("param_row_id"))
        forced_field = str(filters.get("param_field") or "").strip() or None

        selected_source = index.sources_by_id.get(selected_source_id) if selected_source_id is not None else None
        source_catalog = self._build_source_catalog(index)
        intent = await self._llm_normalizer.interpret_query_async(
            query=query,
            candidate_sources=source_catalog,
            selected_source_id=selected_source_id,
            selected_source_title=selected_source.title if selected_source else None,
        )
        requested_field = forced_field or intent.requested_field or self._default_requested_field(
            query=query,
            target_type=intent.target_type,
        )
        intent = intent.model_copy(update={"requested_field": requested_field})

        source_resolution = self._resolve_source(
            query=query,
            raw_query=raw_query,
            index=index,
            intent=intent,
            selected_source_id=selected_source_id,
        )
        if source_resolution.state == "need_clarify":
            return self._build_source_clarify_envelope(query, index, source_resolution, intent)
        if source_resolution.state == "ecu_not_found" or source_resolution.source is None:
            return self._build_source_clarify_envelope(
                query,
                index,
                source_resolution,
                intent,
                reason="ecu_not_found",
                message=self._append_ecu_confirm_hint(
                    source_resolution.message or "本地参数资料库中暂无相关 ECU 资料。"
                ),
            )

        source = source_resolution.source
        source_rows = list(index.rows_by_source.get(source.source_knowledge_id, ()))
        row_resolution = await self._resolve_rows_async(
            query=query,
            source=source,
            rows=source_rows,
            intent=intent,
            selected_row_id=selected_row_id,
        )
        if row_resolution.state == "need_clarify":
            return self._build_row_clarify_envelope(query, source, row_resolution.candidate_rows, intent)
        if row_resolution.state == "missing_target":
            return self._build_row_clarify_envelope(
                query,
                source,
                row_resolution.candidate_rows,
                intent,
                reason="missing_target",
                message=row_resolution.message,
                example_rows=tuple(source_rows),
            )
        if row_resolution.state == "pin_not_found":
            return self._build_no_match_envelope(
                query=query,
                reason="pin_not_found_under_ecu",
                message=row_resolution.message
                or (
                    f"已定位到 ECU 资料《{source.title}》，但没有找到对应的针脚信息。"
                    "请检查针脚输入是否正确后重新发送。"
                ),
                selected_source=source,
            )

        return ToolResultEnvelope(
            status=ToolResultStatus.OK,
            data=self._build_match_payload(query, source, list(row_resolution.rows), requested_field),
        ).model_dump(mode="json")

    def _resolve_source(
        self,
        *,
        query: str,
        raw_query: str | None,
        index: ParameterKnowledgeIndex,
        intent: ParameterQueryIntent,
        selected_source_id: int | None,
    ) -> SourceResolution:
        explicit_source_query = raw_query or query
        explicit_source_ids = self._find_explicit_source_ids(index, explicit_source_query)

        if selected_source_id is not None:
            selected = index.sources_by_id.get(selected_source_id)
            if selected is None:
                return SourceResolution(
                    state="ecu_not_found",
                    message="当前选择的 ECU 资料不存在，请重新查询。",
                )
            if explicit_source_ids:
                non_selected_source_ids = [
                    source_id for source_id in explicit_source_ids if source_id != selected_source_id
                ]
                if len(non_selected_source_ids) == 1:
                    explicit_source = index.sources_by_id.get(non_selected_source_ids[0])
                    if explicit_source is not None:
                        return SourceResolution(state="exact_match", source=explicit_source)
                if len(non_selected_source_ids) > 1:
                    return SourceResolution(
                        state="need_clarify",
                        candidate_source_ids=tuple(non_selected_source_ids[:6]),
                    )
            return SourceResolution(state="exact_match", source=selected)

        if len(explicit_source_ids) == 1:
            explicit_source = index.sources_by_id.get(explicit_source_ids[0])
            if explicit_source is not None:
                return SourceResolution(state="exact_match", source=explicit_source)
        if len(explicit_source_ids) > 1:
            return SourceResolution(state="need_clarify", candidate_source_ids=tuple(explicit_source_ids[:6]))

        validated_candidate_ids = self._valid_source_candidate_ids(index, intent.candidate_source_ids)
        if intent.ecu_source_id is not None:
            selected = index.sources_by_id.get(intent.ecu_source_id)
            if selected is not None:
                return SourceResolution(state="exact_match", source=selected)
            return SourceResolution(
                state="ecu_not_found",
                message=f"本地参数资料库中暂无“{intent.ecu_text or '该 ECU'}”相关资料。",
            )

        if intent.ecu_text:
            return SourceResolution(
                state="ecu_not_found",
                message=f"本地参数资料库中暂无“{intent.ecu_text}”相关 ECU 资料。",
            )

        recovered_candidate_ids = self._infer_source_candidates_from_target(
            index=index,
            query=query,
            intent=intent,
        )

        if intent.need_clarify and intent.clarify_target == "ecu":
            return SourceResolution(
                state="need_clarify",
                candidate_source_ids=tuple((validated_candidate_ids or recovered_candidate_ids)[:6]),
            )

        if len(validated_candidate_ids) == 1:
            source = index.sources_by_id.get(validated_candidate_ids[0])
            if source is not None:
                return SourceResolution(state="exact_match", source=source)

        if len(validated_candidate_ids) > 1:
            return SourceResolution(state="need_clarify", candidate_source_ids=tuple(validated_candidate_ids[:6]))

        if recovered_candidate_ids:
            return SourceResolution(state="need_clarify", candidate_source_ids=tuple(recovered_candidate_ids[:6]))

        return SourceResolution(
            state="need_clarify",
            candidate_source_ids=(),
        )

    def _resolve_rows(
        self,
        *,
        query: str,
        source: ParameterIndexSource,
        rows: list[ParameterIndexRow],
        intent: ParameterQueryIntent,
        selected_row_id: int | None,
    ) -> RowResolution:
        if not rows:
            return RowResolution(
                state="pin_not_found",
                message=f"资料《{source.title}》当前没有可用针脚记录。",
            )

        if selected_row_id is not None:
            selected_row = next((row for row in rows if row.id == selected_row_id), None)
            if selected_row is None:
                return RowResolution(
                    state="pin_not_found",
                    message="当前选择的针脚记录不存在，请重新查询。",
                )
            return RowResolution(state="exact_match", rows=(selected_row,))

        if intent.target_type == "ecu_pin_no" and intent.target_text:
            normalized_pin = normalize_pin_no(intent.target_text)
            matched_rows = [row for row in rows if row.ecu_pin_no_normalized == normalized_pin]
            if not matched_rows:
                return RowResolution(
                    state="pin_not_found",
                    message=(
                        f"已识别 ECU 为 {source.ecu_name or source.title}，但没有找到针脚“{intent.target_text}”对应的信息。"
                        "请检查针脚输入是否正确后重新发送。"
                    ),
                )
            return RowResolution(state="exact_match", rows=tuple(matched_rows))

        if intent.target_type == "connector_pin_no" and intent.target_text:
            normalized_pin = normalize_pin_no(intent.target_text)
            matched_rows = [
                row for row in rows
                if normalize_pin_no(row.connector_pin_no) == normalized_pin
            ]
            if not matched_rows:
                return RowResolution(
                    state="pin_not_found",
                    message=(
                        f"已识别 ECU 为 {source.ecu_name or source.title}，但没有找到接插件针脚“{intent.target_text}”对应的信息。"
                        "请检查输入是否正确后重新发送。"
                    ),
                )
            return RowResolution(state="exact_match", rows=tuple(matched_rows))

        row_selection = self._llm_normalizer.select_rows(
            query=query,
            source_title=source.title,
            source_ecu_name=source.ecu_name,
            requested_field=intent.requested_field,
            target_text=intent.target_text,
            target_type=intent.target_type,
            rows=[self._to_row_candidate(row) for row in rows],
        )
        return self._resolve_row_selection_result(source, rows, intent, row_selection)

    async def _resolve_rows_async(
        self,
        *,
        query: str,
        source: ParameterIndexSource,
        rows: list[ParameterIndexRow],
        intent: ParameterQueryIntent,
        selected_row_id: int | None,
    ) -> RowResolution:
        if not rows:
            return RowResolution(
                state="pin_not_found",
                message=f"资料《{source.title}》当前没有可用针脚记录。",
            )

        if selected_row_id is not None:
            selected_row = next((row for row in rows if row.id == selected_row_id), None)
            if selected_row is None:
                return RowResolution(
                    state="pin_not_found",
                    message="当前选择的针脚记录不存在，请重新查询。",
                )
            return RowResolution(state="exact_match", rows=(selected_row,))

        if intent.target_type == "ecu_pin_no" and intent.target_text:
            normalized_pin = normalize_pin_no(intent.target_text)
            matched_rows = [row for row in rows if row.ecu_pin_no_normalized == normalized_pin]
            if not matched_rows:
                return RowResolution(
                    state="pin_not_found",
                    message=(
                        f"已识别 ECU 为 {source.ecu_name or source.title}，但没有找到针脚“{intent.target_text}”对应的信息。"
                        "请检查针脚输入是否正确后重新发送。"
                    ),
                )
            return RowResolution(state="exact_match", rows=tuple(matched_rows))

        if intent.target_type == "connector_pin_no" and intent.target_text:
            normalized_pin = normalize_pin_no(intent.target_text)
            matched_rows = [
                row for row in rows
                if normalize_pin_no(row.connector_pin_no) == normalized_pin
            ]
            if not matched_rows:
                return RowResolution(
                    state="pin_not_found",
                    message=(
                        f"已识别 ECU 为 {source.ecu_name or source.title}，但没有找到接插件针脚“{intent.target_text}”对应的信息。"
                        "请检查输入是否正确后重新发送。"
                    ),
                )
            return RowResolution(state="exact_match", rows=tuple(matched_rows))

        row_selection = await self._llm_normalizer.select_rows_async(
            query=query,
            source_title=source.title,
            source_ecu_name=source.ecu_name,
            requested_field=intent.requested_field,
            target_text=intent.target_text,
            target_type=intent.target_type,
            rows=[self._to_row_candidate(row) for row in rows],
        )
        return self._resolve_row_selection_result(source, rows, intent, row_selection)

    def _resolve_row_selection_result(
        self,
        source: ParameterIndexSource,
        rows: list[ParameterIndexRow],
        intent: ParameterQueryIntent,
        row_selection: ParameterQueryRowSelection,
    ) -> RowResolution:
        row_by_id = {row.id: row for row in rows}
        selected_rows = [row_by_id[row_id] for row_id in row_selection.row_ids if row_id in row_by_id]

        if row_selection.match_state == "exact_match" and selected_rows:
            return RowResolution(state="exact_match", rows=tuple(selected_rows))

        if row_selection.match_state == "multiple_candidates" and selected_rows:
            return RowResolution(state="need_clarify", candidate_rows=tuple(selected_rows))

        if row_selection.match_state == "missing_target":
            return RowResolution(
                state="missing_target",
                message=(
                    f"已识别 ECU 为 {source.ecu_name or source.title}，但当前问题还缺少具体目标。"
                    "请补充针脚编号、信号名称或零部件名称后再查询。"
                ),
            )

        target_display = intent.target_text or "当前目标"
        return RowResolution(
            state="pin_not_found",
            message=(
                f"已定位到 ECU 资料《{source.title}》，但没有找到“{target_display}”对应的针脚信息。"
                "请检查针脚输入是否正确后重新发送。"
            ),
        )

    def _build_source_catalog(self, index: ParameterKnowledgeIndex) -> list[ParameterQuerySourceCandidate]:
        alias_map = self._source_alias_map(index)
        catalog = []
        for source in sorted(index.sources_by_id.values(), key=lambda item: item.source_knowledge_id):
            catalog.append(
                ParameterQuerySourceCandidate(
                    source_id=source.source_knowledge_id,
                    title=source.title,
                    ecu_name=source.ecu_name,
                    system_voltage=source.system_voltage,
                    row_count=source.parsed_row_count,
                    aliases=alias_map.get(source.source_knowledge_id, []),
                )
            )
        return catalog

    def _build_source_clarify_envelope(
        self,
        query: str,
        index: ParameterKnowledgeIndex,
        resolution: SourceResolution,
        intent: ParameterQueryIntent,
        *,
        reason: str = "missing_ecu",
        message: str | None = None,
    ) -> dict[str, Any]:
        options: list[ClarifyCandidateOption] = []
        max_sources = self._get_int_config("param_query_top_sources", settings.param_query_top_sources)
        for source_id in resolution.candidate_source_ids[:max_sources]:
            source = index.sources_by_id.get(source_id)
            if source is None:
                continue
            description_parts = []
            if source.system_voltage is not None:
                description_parts.append(f"{source.system_voltage}V")
            if source.parsed_row_count:
                description_parts.append(f"{source.parsed_row_count} 条针脚")
            options.append(
                ClarifyCandidateOption(
                    key=str(source.source_knowledge_id),
                    label=source.ecu_name or source.title,
                    description=" · ".join(description_parts) or None,
                    selection_payload=SelectionPayload(
                        filters={
                            "param_source_id": str(source.source_knowledge_id),
                            **({"param_field": intent.requested_field} if intent.requested_field else {}),
                        }
                    ),
                )
            )
        question = "请先确认 ECU 型号" if options else "请确认 ECU 型号"

        return ToolResultEnvelope(
            status=ToolResultStatus.NEED_CLARIFY,
            data={"matched": False, "query": query, "clarify_type": "source", "reason": reason},
            clarify=ClarifyCandidate(
                source="parameter_query",
                question=question,
                results_count=len(options),
                options=options,
                context={
                    "scene": "parameter_query",
                    "clarify_type": "source",
                    "query": query,
                    **({"message": message} if message else {}),
                    "input_hint": "也可以直接输入 ECU 型号",
                },
            ),
        ).model_dump(mode="json")

    def _build_row_clarify_envelope(
        self,
        query: str,
        source: ParameterIndexSource,
        candidate_rows: tuple[ParameterIndexRow, ...],
        intent: ParameterQueryIntent,
        *,
        reason: str = "multiple_row_candidates",
        message: str | None = None,
        example_rows: tuple[ParameterIndexRow, ...] | None = None,
    ) -> dict[str, Any]:
        options = []
        max_rows = self._get_int_config("param_query_top_rows", settings.param_query_top_rows)
        for row in candidate_rows[:max_rows]:
            options.append(
                ClarifyCandidateOption(
                    key=str(row.id),
                    label=self._row_option_label(row),
                    description=self._row_option_description(row),
                    selection_payload=SelectionPayload(
                        filters={
                            "param_source_id": str(source.source_knowledge_id),
                            "param_row_id": str(row.id),
                            **({"param_field": intent.requested_field} if intent.requested_field else {}),
                        }
                    ),
                )
            )
        pin_examples = self._pin_examples(example_rows or candidate_rows)
        input_hint = (
            f"请按当前 ECU 的针脚格式输入，例如：{'、'.join(pin_examples)}"
            if pin_examples
            else "也可以直接输入更准确的针脚编号或信号名称"
        )
        question = "请确认要查的针脚" if options else "请补充要查的具体针脚"
        if pin_examples and not options:
            question = f"{question}，例如 {'、'.join(pin_examples)}"

        return ToolResultEnvelope(
            status=ToolResultStatus.NEED_CLARIFY,
            data={
                "matched": False,
                "query": query,
                "clarify_type": "row",
                "reason": reason,
                "selected_source": self._serialize_source(source),
            },
            clarify=ClarifyCandidate(
                source="parameter_query",
                question=question,
                results_count=len(options),
                options=options,
                context={
                    "scene": "parameter_query",
                    "clarify_type": "row",
                    "query": query,
                    "source_id": str(source.source_knowledge_id),
                    "source_title": source.title,
                    **({"message": message} if message else {}),
                    "input_hint": input_hint,
                    **({"pin_examples": pin_examples} if pin_examples else {}),
                },
            ),
        ).model_dump(mode="json")

    def _build_match_payload(
        self,
        query: str,
        source: ParameterIndexSource,
        rows: list[ParameterIndexRow],
        requested_field: str | None,
    ) -> dict[str, Any]:
        requested_field_label = FIELD_LABELS.get(requested_field or "", "相关参数")
        summary = self._build_summary(source, rows, requested_field)
        return {
            "matched": True,
            "query": query,
            "match_state": "exact_match",
            "summary": summary,
            "requested_field": requested_field,
            "requested_field_label": requested_field_label,
            "selected_source": self._serialize_source(source),
            "rows": [self._serialize_row(row, requested_field) for row in rows],
            "source_refs": [
                {
                    "id": str(source.source_knowledge_id),
                    "title": source.title,
                    "relation": "primary",
                    "match_score": 1.0,
                }
            ],
        }

    def _build_no_match_envelope(
        self,
        *,
        query: str,
        reason: str,
        message: str,
        selected_source: ParameterIndexSource | None = None,
    ) -> dict[str, Any]:
        source_refs = []
        if selected_source is not None:
            source_refs.append(
                {
                    "id": str(selected_source.source_knowledge_id),
                    "title": selected_source.title,
                    "relation": "checked",
                    "match_score": 1.0,
                }
            )
        return ToolResultEnvelope(
            status=ToolResultStatus.OK,
            data={
                "matched": False,
                "query": query,
                "reason": reason,
                "message": message,
                "selected_source": self._serialize_source(selected_source) if selected_source else None,
                "source_refs": source_refs,
            },
        ).model_dump(mode="json")

    def _infer_source_candidates_from_target(
        self,
        *,
        index: ParameterKnowledgeIndex,
        query: str,
        intent: ParameterQueryIntent,
    ) -> list[int]:
        target_hints = self._candidate_target_hints(query=query, intent=intent)
        if not target_hints:
            return []

        source_scores: dict[int, float] = {}
        for row in index.rows_by_id.values():
            best_score = 0.0
            for hint in target_hints:
                score = self._score_row_target_hint(row=row, hint=hint)
                if score > best_score:
                    best_score = score
            if best_score < 0.72:
                continue
            existing = source_scores.get(row.source_knowledge_id, 0.0)
            source_scores[row.source_knowledge_id] = max(existing, best_score)

        ranked = sorted(source_scores.items(), key=lambda item: (-item[1], item[0]))
        return [source_id for source_id, _ in ranked]

    @staticmethod
    def _candidate_target_hints(*, query: str, intent: ParameterQueryIntent) -> list[str]:
        hints: list[str] = []
        normalized_query = normalize_text(query)
        candidate_values = [
            intent.target_text
            if intent.target_text and normalize_text(intent.target_text) in normalized_query
            else None,
            normalize_free_text_hint(query),
        ]
        for value in candidate_values:
            cleaned = str(value or "").strip()
            normalized = normalize_text(cleaned)
            if len(normalized) < 2:
                continue
            if normalized in {normalize_text(item) for item in hints}:
                continue
            hints.append(cleaned)
        return hints

    @staticmethod
    def _score_row_target_hint(*, row: ParameterIndexRow, hint: str) -> float:
        normalized_hint = normalize_text(hint)
        if not normalized_hint:
            return 0.0

        candidate_texts = (
            row.component_name,
            row.pin_definition,
            row.ecu_pin_no,
            row.connector_pin_no,
            row.remark,
        )
        best_score = 0.0
        for candidate in candidate_texts:
            normalized_candidate = normalize_text(candidate)
            if not normalized_candidate:
                continue
            if normalized_hint == normalized_candidate:
                best_score = max(best_score, 1.0)
                continue
            if normalized_hint in normalized_candidate or normalized_candidate in normalized_hint:
                best_score = max(best_score, 0.92)
                continue
            similarity = text_similarity(hint, candidate)
            if similarity > best_score:
                best_score = similarity
        return best_score

    def _build_summary(
        self,
        source: ParameterIndexSource,
        rows: list[ParameterIndexRow],
        requested_field: str | None,
    ) -> str:
        if not rows:
            return "未命中参数记录。"
        if len(rows) > 1:
            return f"命中《{source.title}》中的 {len(rows)} 条相关针脚记录。"

        row = rows[0]
        label = FIELD_LABELS.get(requested_field or "", "")
        if requested_field == "voltage":
            voltage_summary = self._row_field_value(row, requested_field)
            if voltage_summary:
                return f"{row.component_name or row.ecu_pin_no or '该针脚'} 的电压信息为 {voltage_summary}。"
        if requested_field == "ecu_pin_no" and row.ecu_pin_no:
            return f"{row.component_name or row.pin_definition or '目标项'} 的 ECU 针脚为 {row.ecu_pin_no}。"
        if requested_field == "pin_definition" and row.pin_definition:
            return f"{row.ecu_pin_no or row.component_name or '该针脚'} 的针脚定义为 {row.pin_definition}。"
        value = self._row_field_value(row, requested_field)
        if value and label:
            return f"{row.component_name or row.ecu_pin_no or '该针脚'} 的 {label}为 {value}。"
        if row.component_name and row.ecu_pin_no:
            return f"已命中 {row.component_name}，对应 ECU 针脚 {row.ecu_pin_no}。"
        return f"已命中《{source.title}》中的相关针脚记录。"

    def _to_row_candidate(self, row: ParameterIndexRow) -> ParameterQueryRowCandidate:
        return ParameterQueryRowCandidate(
            row_id=row.id,
            ecu_pin_no=row.ecu_pin_no,
            component_name=row.component_name,
            pin_definition=row.pin_definition,
            connector_pin_no=row.connector_pin_no,
            open_voltage_text=row.open_voltage_text,
            static_voltage_text=row.static_voltage_text,
            idle_voltage_text=row.idle_voltage_text,
            remark=row.remark,
        )

    def _source_alias_map(self, index: ParameterKnowledgeIndex) -> dict[int, list[str]]:
        alias_map: dict[int, list[str]] = {}
        for alias_entries in (index.alias_lookup.get("ecu") or {}).values():
            for entry in alias_entries:
                if entry.source_knowledge_id is None:
                    continue
                source_aliases = alias_map.setdefault(entry.source_knowledge_id, [])
                if entry.alias_value not in source_aliases:
                    source_aliases.append(entry.alias_value)
        return alias_map

    def _find_explicit_source_ids(self, index: ParameterKnowledgeIndex, query: str) -> list[int]:
        normalized_query = normalize_text(query)
        if not normalized_query:
            return []

        alias_map = self._source_alias_map(index)
        matched: list[tuple[int, int]] = []
        for source in index.sources_by_id.values():
            tokens: list[str] = []
            seen_tokens: set[str] = set()
            raw_values: list[str] = []
            if source.ecu_name:
                raw_values.append(source.ecu_name)
            raw_values.extend(alias_map.get(source.source_knowledge_id, []))
            raw_values.append(source.title)
            for raw_value in raw_values:
                for token in self._expand_source_match_tokens(raw_value):
                    if token in seen_tokens:
                        continue
                    seen_tokens.add(token)
                    tokens.append(token)

            best_len = 0
            for token in tokens:
                if not token or len(token) < 4:
                    continue
                if token in normalized_query:
                    best_len = max(best_len, len(token))
            if best_len > 0:
                matched.append((source.source_knowledge_id, best_len))

        matched.sort(key=lambda item: (-item[1], item[0]))
        return [source_id for source_id, _ in matched]

    @staticmethod
    def _expand_source_match_tokens(value: str | None) -> list[str]:
        text = str(value or "").strip()
        if not text:
            return []

        candidates = [text]
        simplified = re.sub(r"针脚(?:电压|定义|信息).*$", "", text, flags=re.IGNORECASE).strip()
        simplified = re.sub(r"[（(].*$", "", simplified).strip(" -_[]()（）【】")
        if simplified and simplified != text:
            candidates.append(simplified)

        normalized_tokens: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = normalize_text(candidate)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_tokens.append(normalized)
        return normalized_tokens

    @staticmethod
    def _valid_source_candidate_ids(index: ParameterKnowledgeIndex, values: list[int] | tuple[int, ...]) -> list[int]:
        valid_ids = []
        seen: set[int] = set()
        for value in values:
            if value in seen:
                continue
            if value not in index.sources_by_id:
                continue
            seen.add(value)
            valid_ids.append(value)
        return valid_ids

    @staticmethod
    def _default_requested_field(*, query: str, target_type: str) -> str:
        extracted = extract_requested_field(query)
        if extracted:
            return extracted
        if any(token in query for token in ("电压", "几伏", "多少伏")):
            return "voltage"
        if any(token in query for token in ("哪个针脚", "在哪个针脚", "几号脚", "脚位")):
            return "ecu_pin_no"
        if any(token in query for token in ("作用", "定义", "什么意思")):
            return "pin_definition"
        if target_type in {"signal", "component"}:
            return "ecu_pin_no"
        return "pin_definition"

    @staticmethod
    def _selection_filters(selection_payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(selection_payload, dict):
            return {}
        filters = selection_payload.get("filters")
        if not isinstance(filters, dict):
            return {}
        return dict(filters)

    @staticmethod
    def _serialize_source(source: ParameterIndexSource | None) -> dict[str, Any] | None:
        if source is None:
            return None
        return {
            "id": str(source.source_knowledge_id),
            "title": source.title,
            "ecu_name": source.ecu_name,
            "system_voltage": source.system_voltage,
            "pin_doc_kind": source.pin_doc_kind,
        }

    def _serialize_row(self, row: ParameterIndexRow, requested_field: str | None) -> dict[str, Any]:
        value = self._row_field_value(row, requested_field)
        return {
            "id": str(row.id),
            "row_no": row.row_no,
            "component_name": row.component_name,
            "ecu_pin_no": row.ecu_pin_no,
            "pin_definition": row.pin_definition,
            "connector_pin_no": row.connector_pin_no,
            "open_voltage_text": row.open_voltage_text,
            "static_voltage_text": row.static_voltage_text,
            "idle_voltage_text": row.idle_voltage_text,
            "remark": row.remark,
            "requested_value": value,
        }

    @staticmethod
    def _row_field_value(row: ParameterIndexRow, field: str | None) -> str | None:
        if field == "pin_definition":
            return row.pin_definition
        if field == "ecu_pin_no":
            return row.ecu_pin_no
        if field == "connector_pin_no":
            return row.connector_pin_no
        if field == "voltage":
            parts = []
            if row.open_voltage_text:
                parts.append(f"开路 {row.open_voltage_text}")
            if row.static_voltage_text:
                parts.append(f"静态 {row.static_voltage_text}")
            if row.idle_voltage_text:
                parts.append(f"怠速 {row.idle_voltage_text}")
            return " / ".join(parts) or None
        if field == "open_voltage":
            return row.open_voltage_text
        if field == "static_voltage":
            return row.static_voltage_text
        if field == "idle_voltage":
            return row.idle_voltage_text
        if field == "remark":
            return row.remark
        return None

    @staticmethod
    def _row_option_label(row: ParameterIndexRow) -> str:
        parts = [item for item in [row.ecu_pin_no, row.component_name, row.pin_definition] if item]
        return " / ".join(parts) or f"第 {row.row_no} 行"

    @staticmethod
    def _row_option_description(row: ParameterIndexRow) -> str | None:
        parts = [item for item in [row.connector_pin_no, row.open_voltage_text, row.static_voltage_text] if item]
        if not parts:
            return None
        return " · ".join(parts)

    @staticmethod
    def _append_ecu_confirm_hint(message: str) -> str:
        normalized = str(message or "").strip()
        if not normalized:
            return "请确认 ECU 型号。"
        if "请确认 ECU 型号" in normalized:
            return normalized
        return f"{normalized.rstrip('。')}，请确认 ECU 型号。"

    @staticmethod
    def _pin_examples(rows: tuple[ParameterIndexRow, ...] | list[ParameterIndexRow]) -> list[str]:
        examples: list[str] = []
        seen: set[str] = set()
        for row in rows:
            pin_no = str(row.ecu_pin_no or "").strip()
            if not pin_no or pin_no in seen:
                continue
            seen.add(pin_no)
            examples.append(pin_no)
            if len(examples) >= 3:
                break
        return examples

    @staticmethod
    def _dedupe_ids(values: list[int] | tuple[int, ...]) -> list[int]:
        seen: set[int] = set()
        ordered: list[int] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value))
        except Exception:
            return None

    def _get_config(self, key: str, default: Any) -> Any:
        if self._config_service is None:
            return default
        return self._config_service.get(key, default)

    def _get_bool_config(self, key: str, default: bool) -> bool:
        return bool(self._get_config(key, default))

    def _get_int_config(self, key: str, default: int) -> int:
        return int(self._get_config(key, default))
