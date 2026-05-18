"""Runtime helpers that adapt doc_search tool traces into frontend responses."""

from typing import Any, Sequence
from uuid import uuid4

from pydantic_ai.messages import ModelRequest, ModelMessage, ToolReturnPart

from app.agent.ask_user_v2 import (
    attach_form_to_ask_user,
    build_single_field_form,
    extract_form_answer_fields,
    extract_primary_answer_text,
)
from app.agent.memory.deferred_store import DeferredState
from app.agent.models.ask_user import AskUserInputType, AskUserOption, AskUserQuestion
from app.schemas.chat import AskUserAnswer


DOC_SEARCH_DEFERRED_TOOL_NAME = "doc_search_clarify"


class DocSearchResponseAdapter:
    """Translate migrated doc_search tool outputs into stable frontend payloads."""

    @staticmethod
    def extract_latest_tool_envelope(
        messages: Sequence[ModelMessage],
        tool_name: str,
    ) -> dict[str, Any] | None:
        for message in reversed(messages):
            if not isinstance(message, ModelRequest):
                continue
            for part in reversed(message.parts):
                if isinstance(part, ToolReturnPart) and part.tool_name == tool_name and isinstance(part.content, dict):
                    return part.content
        return None

    @staticmethod
    def build_documents_content(search_data: dict[str, Any]) -> dict[str, Any]:
        raw_results = search_data.get("results") or []
        formatted_results: list[dict[str, Any]] = []
        for item in raw_results:
            formatted_results.append(
                {
                    "file_id": item.get("file_id"),
                    "filename": item.get("filename") or item.get("title"),
                    "physical_path": item.get("physical_path") or item.get("path"),
                    "file_type": item.get("file_type"),
                    "brand": item.get("brand"),
                    "series": item.get("series"),
                    "model": item.get("model"),
                    "hierarchy_full": item.get("hierarchy_full"),
                    "score": item.get("score"),
                    "ref_file_id": item.get("ref_file_id"),
                    "parent_id": item.get("parent_id"),
                    "pic_folder_url": item.get("pic_folder_url"),
                    "ggzj_sn": item.get("ggzj_sn"),
                    "ggzj_data_type": item.get("ggzj_data_type"),
                    "ggzj_file_no": item.get("ggzj_file_no"),
                    "ggzj_file_type": item.get("ggzj_file_type"),
                }
            )

        total_hits = int(search_data.get("total") or len(raw_results))
        returned_count = len(formatted_results)
        summary = search_data.get("summary") or f"找到 {total_hits} 个相关文档"

        return {
            "query": search_data.get("original_query") or search_data.get("query") or "",
            "total": total_hits,
            "total_hits": total_hits,
            "returned_count": returned_count,
            "page_size": returned_count,
            "results": formatted_results,
            "filters": search_data.get("applied_filters") or {},
            "summary": summary,
            "planned_queries": search_data.get("planned_queries") or [],
            "query_plan_rationale": search_data.get("query_plan_rationale") or "",
        }

    @staticmethod
    def build_invalid_message_content(search_data: dict[str, Any]) -> dict[str, Any]:
        validity = search_data.get("validity") or {}
        existence = validity.get("existence") or {}

        content: dict[str, Any] = {
            "message": validity.get("message") or "未找到相关资料。",
            "should_archive_previous": True,
        }
        if existence:
            content["existence_info"] = {
                "status": existence.get("status"),
                "message": existence.get("message"),
                "suggestions": existence.get("suggestions") or {},
                "unmatched_entities": existence.get("unmatched_entities") or {},
            }
        return content

    @staticmethod
    def build_ask_user_question(
        analysis_envelope: dict[str, Any],
        *,
        tool_call_id: str | None = None,
    ) -> AskUserQuestion:
        clarify = analysis_envelope.get("clarify") or {}
        context = clarify.get("context") or (analysis_envelope.get("data") or {}).get("context") or {}
        options = [
            AskUserOption.model_validate(option)
            for option in (clarify.get("options") or [])
        ]
        ask_user = AskUserQuestion(
            tool_call_id=tool_call_id or f"doc_search_{uuid4().hex}",
            question=clarify.get("question") or context.get("message") or "请补充筛选条件",
            input_type=AskUserInputType.SINGLE_SELECT,
            options=options,
            allow_free_input=False,
            context=context,
        )
        form = build_single_field_form(
            form_id=f"doc_search_form_{ask_user.tool_call_id}",
            title="资料筛选条件",
            description="先确认最接近的筛选项，再继续搜索。",
            ask_reason=context.get("message") or "当前搜索结果仍需进一步收敛。",
            field_key=str(context.get("facet") or "clarify_choice"),
            field_label=clarify.get("question") or "请选择筛选项",
            input_type=AskUserInputType.SINGLE_SELECT,
            options=options,
            allow_free_input=False,
            auto_submit_single_select=True,
        )
        return attach_form_to_ask_user(
            ask_user,
            form=form,
            scene="doc_search",
            extra_context={"facet": context.get("facet") or "clarify_choice"},
        )

    @staticmethod
    def build_deferred_state(
        *,
        tool_call_id: str,
        message_history_json: str,
        query: str,
        clarify_round: int,
        ask_user: AskUserQuestion,
        search_snapshot: dict[str, Any] | None = None,
    ) -> DeferredState:
        return DeferredState(
            tool_call_id=tool_call_id,
            tool_name=DOC_SEARCH_DEFERRED_TOOL_NAME,
            message_history_json=message_history_json,
            payload={
                "query": query,
                "clarify_round": clarify_round,
                "ask_user": ask_user.model_dump(mode="json"),
                "search_snapshot": DocSearchResponseAdapter.build_search_snapshot(search_snapshot),
            },
        )

    @staticmethod
    def build_search_snapshot(search_data: dict[str, Any] | None) -> dict[str, Any]:
        if not search_data:
            return {}

        return {
            "query": search_data.get("query") or search_data.get("original_query") or "",
            "original_query": search_data.get("original_query") or search_data.get("query") or "",
            "results": search_data.get("results") or [],
            "preprocessing": search_data.get("preprocessing"),
            "search_method": search_data.get("search_method"),
            "search_time_ms": search_data.get("search_time_ms"),
            "planned_queries": search_data.get("planned_queries") or [],
            "query_plan_rationale": search_data.get("query_plan_rationale") or "",
        }

    @staticmethod
    def resolve_search_snapshot(deferred_state: DeferredState) -> dict[str, Any] | None:
        snapshot = deferred_state.payload.get("search_snapshot")
        if isinstance(snapshot, dict) and snapshot.get("results") is not None:
            return snapshot
        return None

    @staticmethod
    def resolve_selection_payload(
        answer: AskUserAnswer,
        deferred_state: DeferredState,
    ) -> dict[str, Any] | None:
        metadata_payload = answer.metadata.get("selection_payload") if answer.metadata else None
        if isinstance(metadata_payload, dict) and metadata_payload:
            return metadata_payload

        ask_user_payload = deferred_state.payload.get("ask_user") or {}
        fields = extract_form_answer_fields(answer.answer)
        answer_text = extract_primary_answer_text(answer.answer)
        if fields:
            for item in fields.values():
                for selected in item.get("selected") or []:
                    for option in ask_user_payload.get("options", []):
                        if selected in {str(option.get("label", "")), str(option.get("key", ""))}:
                            selection_payload = option.get("selection_payload")
                            if isinstance(selection_payload, dict):
                                return selection_payload
        for option in ask_user_payload.get("options", []):
            if answer_text in {str(option.get("label", "")), str(option.get("key", ""))}:
                selection_payload = option.get("selection_payload")
                if isinstance(selection_payload, dict):
                    return selection_payload
        return None
