"""Case context lifecycle and update helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any, Iterable
from uuid import uuid4

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from app.agent.context.guard import LoopGuard
from app.agent.context.models import (
    CaseContext,
    CaseContextArtifact,
    CaseContextArtifactType,
    CaseContextAttemptedAction,
    CaseContextCandidateAnswer,
    CaseContextRemainingBudget,
    CaseContextPendingAction,
    utcnow_iso,
)
from app.agent.context.store import CaseContextStore
from app.agent.models.ask_user import AskUserQuestion
from app.schemas.chat import AskUserAnswer, ChatRequest, ChatResponse


class CaseContextManager:
    """Load, update, compact, and persist shared case context."""

    def __init__(
        self,
        store: CaseContextStore,
        *,
        max_artifacts_total: int = 24,
        max_artifacts_per_type: int = 6,
        max_attempted_actions: int = 8,
        max_selected_docs: int = 10,
        max_serialized_bytes: int = 40_960,
    ) -> None:
        self._store = store
        self._max_artifacts_total = max_artifacts_total
        self._max_artifacts_per_type = max_artifacts_per_type
        self._max_attempted_actions = max_attempted_actions
        self._max_selected_docs = max_selected_docs
        self._max_serialized_bytes = max_serialized_bytes

    def load(self, session_id: str) -> CaseContext:
        context = self._store.load(session_id)
        if context is None:
            return CaseContext(session_id=session_id)
        return self._compact_internal(context)

    def save(self, context: CaseContext) -> CaseContext:
        compacted = self._compact_internal(context)
        compacted.revision += 1
        compacted.updated_at = utcnow_iso()
        self._store.save(compacted)
        return compacted

    def reset(self, session_id: str) -> CaseContext:
        self._store.clear(session_id)
        context = CaseContext(session_id=session_id)
        self._store.save(context)
        return context

    def attach_runtime_state(
        self,
        context: CaseContext,
        *,
        loop_guard: Any | None = None,
    ) -> CaseContext:
        updated = context.model_copy(deep=True)
        self._apply_loop_guard_state(updated, loop_guard)
        return self._compact_internal(updated)

    def record_user_answer(
        self,
        context: CaseContext,
        answer: AskUserAnswer,
        *,
        business: str | None = None,
    ) -> CaseContext:
        active_business = business or (context.pending_action.business if context.pending_action is not None else "AGENT_LOOP")
        selection_payload = answer.metadata.get("selection_payload") if answer.metadata else None
        derived_slots = self._derive_slots_from_selection_payload(selection_payload)
        summary = f"用户补充信息：{self._stringify_answer(answer.answer)}"
        updated = self._append_artifact(
            context,
            CaseContextArtifact(
                artifact_id=f"ctx_{uuid4().hex}",
                type=CaseContextArtifactType.USER_ANSWER,
                source_business=active_business,
                summary=summary,
                structured_data={
                    "answer": answer.answer,
                    "selection_payload": selection_payload or {},
                    "tool_call_id": answer.tool_call_id,
                },
                derived_slots=derived_slots,
                confidence=1.0,
            ),
        )
        if updated.pending_action is not None and updated.pending_action.tool_call_id == answer.tool_call_id:
            updated.pending_action = None
        return self._compact_internal(updated)

    def record_pending_action(
        self,
        context: CaseContext,
        *,
        ask_user: AskUserQuestion,
        business: str,
        scene: str,
    ) -> CaseContext:
        options_summary = [option.label for option in ask_user.options[:5]]
        updated = self._append_artifact(
            context,
            CaseContextArtifact(
                artifact_id=f"ctx_{uuid4().hex}",
                type=CaseContextArtifactType.PENDING_ACTION,
                source_business=business,
                summary=f"{business} 待澄清：{ask_user.question}",
                structured_data={
                    "tool_call_id": ask_user.tool_call_id,
                    "question": ask_user.question,
                    "input_type": ask_user.input_type.value,
                    "options": options_summary,
                    "scene": scene,
                    "context": ask_user.context or {},
                },
                derived_slots={},
                confidence=1.0,
            ),
        )
        updated.pending_action = CaseContextPendingAction(
            scene=scene,
            tool_call_id=ask_user.tool_call_id,
            business=business,
            question=ask_user.question,
            options_summary=options_summary,
            context=ask_user.context or {},
        )
        return self._compact_internal(updated)

    def record_doc_search_response(
        self,
        context: CaseContext,
        *,
        request: ChatRequest,
        response: ChatResponse,
    ) -> CaseContext:
        if response.type == "ask_user" and response.ask_user is not None:
            derived_slots = self._derive_doc_search_slots_from_ask_user(response)
            updated = self._append_artifact(
                context,
                CaseContextArtifact(
                    artifact_id=f"ctx_{uuid4().hex}",
                    type=CaseContextArtifactType.DOC_SEARCH_RESULT,
                    source_business="DOC_SEARCH",
                    summary=f"资料搜索待澄清：{response.ask_user.question}",
                    structured_data={
                        "query": request.message,
                        "question": response.ask_user.question,
                        "options": [option.label for option in response.ask_user.options[:5]],
                    },
                    derived_slots=derived_slots,
                    confidence=0.95,
                ),
            )
            return self.record_pending_action(
                updated,
                ask_user=response.ask_user,
                business="DOC_SEARCH",
                scene="doc_search",
            )

        if response.type == "documents":
            content = response.content if isinstance(response.content, dict) else {}
            results = content.get("results") or []
            derived_slots = self._derive_slots_from_filters(content.get("filters"))
            derived_slots["selected_doc_ids"] = [str(item.get("file_id")) for item in results[: self._max_selected_docs] if item.get("file_id")]
            derived_slots["selected_doc_titles"] = [
                str(item.get("filename"))
                for item in results[: min(5, self._max_selected_docs)]
                if item.get("filename")
            ]
            updated = self._append_artifact(
                context,
                CaseContextArtifact(
                    artifact_id=f"ctx_{uuid4().hex}",
                    type=CaseContextArtifactType.DOC_SEARCH_RESULT,
                    source_business="DOC_SEARCH",
                    summary=f"资料搜索命中 {int(content.get('returned_count') or 0)} 条：{content.get('summary') or content.get('query') or ''}".strip("："),
                    structured_data={
                        "query": content.get("query"),
                        "filters": content.get("filters") or {},
                        "total": content.get("total"),
                        "returned_count": content.get("returned_count"),
                        "results": [
                            {
                                "file_id": item.get("file_id"),
                                "filename": item.get("filename"),
                                "brand": item.get("brand"),
                                "series": item.get("series"),
                            }
                            for item in results[:5]
                        ],
                    },
                    derived_slots=derived_slots,
                    confidence=1.0,
                ),
            )
            updated.pending_action = None
            return self._compact_internal(updated)

        if response.type == "message":
            content = response.content if isinstance(response.content, dict) else {"message": response.content}
            updated = self._append_artifact(
                context,
                CaseContextArtifact(
                    artifact_id=f"ctx_{uuid4().hex}",
                    type=CaseContextArtifactType.DOC_SEARCH_RESULT,
                    source_business="DOC_SEARCH",
                    summary=f"资料搜索结果：{content.get('message') or '未找到相关资料。'}",
                    structured_data={"query": request.message, "message": content.get("message")},
                    derived_slots={},
                    confidence=1.0,
                ),
            )
            updated.pending_action = None
            return self._compact_internal(updated)

        return self._compact_internal(context)

    def record_image_evidence(
        self,
        context: CaseContext,
        *,
        evidence: dict[str, Any],
    ) -> CaseContext:
        derived_slots = self._derive_slots_from_image_evidence(evidence)
        summary = str(evidence.get("summary") or "").strip() or "图片证据已识别。"
        updated = self._append_artifact(
            context,
            CaseContextArtifact(
                artifact_id=str(evidence.get("image_evidence_id") or f"ctx_{uuid4().hex}"),
                type=CaseContextArtifactType.IMAGE_EVIDENCE,
                source_business=self._infer_image_evidence_business(evidence),
                summary=f"图片证据：{summary}",
                structured_data=evidence,
                derived_slots=derived_slots,
                confidence=float(evidence.get("confidence") or 0.0),
            ),
        )
        return self._compact_internal(updated)

    def record_parameter_query_envelope(
        self,
        context: CaseContext,
        *,
        query: str,
        selection_payload: dict[str, Any] | None,
        envelope: dict[str, Any],
        ask_user: AskUserQuestion | None = None,
        loop_guard: Any | None = None,
    ) -> CaseContext:
        updated = context.model_copy(deep=True)
        artifact = self._artifact_from_tool_envelope("query_parameters", envelope)
        if artifact is not None:
            updated = self._append_artifact(updated, artifact)

        updated.attempted_actions.append(
            self._build_attempted_action(
                tool_name="query_parameters",
                args={
                    "query": query,
                    "selection_payload": selection_payload or {},
                },
                envelope=envelope,
            )
        )
        if len(updated.attempted_actions) > self._max_attempted_actions:
            updated.attempted_actions = updated.attempted_actions[-self._max_attempted_actions :]

        updated.pending_action = None
        updated = self.attach_runtime_state(updated, loop_guard=loop_guard)
        if ask_user is not None:
            updated = self.record_pending_action(
                updated,
                ask_user=ask_user,
                business="PARAM_QUERY",
                scene=(ask_user.context or {}).get("scene") or "parameter_query",
            )
            updated = self.attach_runtime_state(updated, loop_guard=loop_guard)
        return self._compact_internal(updated)

    def record_run_messages(
        self,
        context: CaseContext,
        *,
        run_messages: Iterable[Any],
        loop_guard: Any | None = None,
    ) -> CaseContext:
        latest_envelopes: dict[str, dict[str, Any]] = {}
        pending_calls_by_id: dict[str, dict[str, Any]] = {}
        pending_calls_by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
        attempted_actions: list[CaseContextAttemptedAction] = []
        for message in run_messages:
            if isinstance(message, ModelResponse):
                for part in message.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    call_state = {
                        "tool_name": part.tool_name,
                        "args": part.args_as_dict() or {},
                    }
                    pending_calls_by_tool[part.tool_name].append(call_state)
                    if part.tool_call_id:
                        pending_calls_by_id[part.tool_call_id] = call_state
                continue
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and isinstance(part.content, dict):
                    latest_envelopes[part.tool_name] = part.content
                    call_state = pending_calls_by_id.pop(part.tool_call_id, None) if part.tool_call_id else None
                    if call_state is None:
                        queued_calls = pending_calls_by_tool.get(part.tool_name) or []
                        if queued_calls:
                            call_state = queued_calls.pop(0)
                    attempted_actions.append(
                        self._build_attempted_action(
                            tool_name=part.tool_name,
                            args=(call_state or {}).get("args") or {},
                            envelope=part.content,
                        )
                    )

        updated = context
        for tool_name in [
            "lookup_ecu_candidates",
            "dtc_diagnosis",
            "query_parameters",
            "lookup_repair_knowledge_titles",
            "get_repair_knowledge_context",
        ]:
            envelope = latest_envelopes.get(tool_name)
            if envelope is None:
                continue
            artifact = self._artifact_from_tool_envelope(tool_name, envelope)
            if artifact is None:
                continue
            updated = self._append_artifact(updated, artifact)

        if attempted_actions:
            updated = updated.model_copy(deep=True)
            updated.attempted_actions.extend(attempted_actions)

        self._apply_loop_guard_state(updated, loop_guard)
        return self._compact_internal(updated)

    def compact(self, context: CaseContext) -> CaseContext:
        return self._compact_internal(context)

    def _compact_internal(self, context: CaseContext) -> CaseContext:
        updated = context.model_copy(deep=True)

        artifacts_by_type: dict[str, list[CaseContextArtifact]] = defaultdict(list)
        for artifact in updated.artifacts:
            artifacts_by_type[artifact.type.value].append(artifact)

        kept: list[CaseContextArtifact] = []
        for artifacts in artifacts_by_type.values():
            kept.extend(artifacts[-self._max_artifacts_per_type :])
        kept.sort(key=lambda item: item.created_at)
        updated.artifacts = kept[-self._max_artifacts_total :]
        updated.attempted_actions = updated.attempted_actions[-self._max_attempted_actions :]

        updated.latest_by_type = {}
        for artifact in updated.artifacts:
            updated.latest_by_type[artifact.type.value] = artifact.artifact_id

        updated.slots.selected_doc_ids = updated.slots.selected_doc_ids[: self._max_selected_docs]
        updated.slots.selected_doc_titles = updated.slots.selected_doc_titles[: min(5, self._max_selected_docs)]

        self._refresh_working_state(updated)
        self._refresh_budget_state(updated)
        if updated.budgets.serialized_bytes <= self._max_serialized_bytes:
            return updated

        trimmed_artifacts: list[CaseContextArtifact] = []
        for artifact in updated.artifacts:
            compact_artifact = artifact.model_copy(deep=True)
            compact_artifact.summary = compact_artifact.summary[:160]
            compact_artifact.source_refs = compact_artifact.source_refs[:1]
            if compact_artifact.type in {
                CaseContextArtifactType.REPAIR_KNOWLEDGE_RESULT,
                CaseContextArtifactType.PARAMETER_RESULT,
                CaseContextArtifactType.DIAGNOSIS_RESULT,
            }:
                compact_artifact.structured_data = self._trim_dict(compact_artifact.structured_data, max_items=6)
            trimmed_artifacts.append(compact_artifact)
        updated.artifacts = trimmed_artifacts
        trimmed_actions: list[CaseContextAttemptedAction] = []
        for action in updated.attempted_actions[-min(4, self._max_attempted_actions) :]:
            compact_action = action.model_copy(deep=True)
            compact_action.result_summary = compact_action.result_summary[:120]
            compact_action.filled_slots = compact_action.filled_slots[:3]
            trimmed_actions.append(compact_action)
        updated.attempted_actions = trimmed_actions
        if updated.candidate_answer is not None:
            updated.candidate_answer.summary = updated.candidate_answer.summary[:160]
        self._refresh_working_state(updated)
        self._refresh_budget_state(updated)

        while updated.artifacts and updated.budgets.serialized_bytes > self._max_serialized_bytes:
            updated.artifacts.pop(0)
            self._refresh_working_state(updated)
            self._refresh_budget_state(updated)

        updated.latest_by_type = {}
        for artifact in updated.artifacts:
            updated.latest_by_type[artifact.type.value] = artifact.artifact_id
        return updated

    @staticmethod
    def build_parameter_selection_payload(
        context: CaseContext | None,
        selection_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload = deepcopy(selection_payload or {})
        if context is None:
            return payload or None

        filters = dict(payload.get("filters") or {})
        if context.slots.brand and "brand" not in filters:
            filters["brand"] = context.slots.brand
        if context.slots.series and "series" not in filters:
            filters["series"] = context.slots.series
        if context.slots.model and "model" not in filters:
            filters["model"] = context.slots.model
        if context.slots.parameter_source_id and "param_source_id" not in filters:
            filters["param_source_id"] = context.slots.parameter_source_id
        if filters:
            payload["filters"] = filters

        file_ids = list(payload.get("file_ids") or [])
        if not file_ids and context.slots.selected_doc_ids:
            payload["file_ids"] = list(context.slots.selected_doc_ids[:5])
        return payload or None

    @staticmethod
    def build_parameter_query_with_context(context: CaseContext | None, query: str) -> str:
        if context is None:
            return query

        parts: list[str] = []
        lowered_query = str(query or "").lower()
        for value in [
            context.slots.brand,
            context.slots.series,
            context.slots.model,
            context.slots.engine,
            context.slots.emission,
            context.slots.ecu_model,
        ]:
            if not value:
                continue
            text = str(value).strip()
            if not text or text.lower() in lowered_query:
                continue
            parts.append(text)
        if not parts:
            return query
        return " ".join(parts + [str(query or "").strip()]).strip()

    def _append_artifact(self, context: CaseContext, artifact: CaseContextArtifact) -> CaseContext:
        updated = context.model_copy(deep=True)
        previous_artifact_id = updated.latest_by_type.get(artifact.type.value)
        artifact.supersedes = previous_artifact_id
        updated.artifacts.append(artifact)
        updated.latest_by_type[artifact.type.value] = artifact.artifact_id
        self._apply_derived_slots(updated, artifact.derived_slots)
        return updated

    def _build_attempted_action(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        envelope: dict[str, Any],
    ) -> CaseContextAttemptedAction:
        artifact = self._artifact_from_tool_envelope(tool_name, envelope)
        return CaseContextAttemptedAction(
            action=tool_name,
            args_signature=LoopGuard._hash_args(args or {}),
            result_summary=(
                artifact.summary
                if artifact is not None
                else self._default_attempted_action_summary(tool_name, envelope)
            ),
            info_gain=LoopGuard._infer_info_gain(envelope),
            filled_slots=sorted((artifact.derived_slots or {}).keys()) if artifact is not None else [],
        )

    def _artifact_from_tool_envelope(
        self,
        tool_name: str,
        envelope: dict[str, Any],
    ) -> CaseContextArtifact | None:
        status = envelope.get("status")
        data = envelope.get("data") or {}

        if tool_name == "lookup_ecu_candidates":
            derived_slots = {"fault_code": data.get("fault_code")}
            if data.get("auto_selected_ecu"):
                derived_slots["ecu_model"] = data.get("auto_selected_ecu")
            summary = data.get("message") or f"识别故障码 {data.get('fault_code') or ''} ECU 候选。"
            return CaseContextArtifact(
                artifact_id=f"ctx_{uuid4().hex}",
                type=CaseContextArtifactType.DIAGNOSIS_RESULT,
                source_business="FAULT_DIAGNOSIS",
                summary=summary,
                structured_data={
                    "tool_name": tool_name,
                    "status": status,
                    "fault_code": data.get("fault_code"),
                    "candidates": data.get("candidates") or [],
                    "count": data.get("count"),
                    "auto_selected_ecu": data.get("auto_selected_ecu"),
                },
                derived_slots={key: value for key, value in derived_slots.items() if value},
                confidence=1.0,
            )

        if tool_name == "dtc_diagnosis":
            summary = data.get("message") or f"完成 {data.get('fault_code') or ''} 的故障诊断。"
            return CaseContextArtifact(
                artifact_id=f"ctx_{uuid4().hex}",
                type=CaseContextArtifactType.DIAGNOSIS_RESULT,
                source_business="FAULT_DIAGNOSIS",
                summary=summary,
                structured_data={
                    "tool_name": tool_name,
                    "status": status,
                    "fault_code": data.get("fault_code"),
                    "ecu_model": data.get("ecu_model"),
                    "state": data.get("state"),
                    "report_url": data.get("report_url"),
                    "task_id": data.get("task_id"),
                    "subscribe_url": data.get("subscribe_url"),
                    "report_id": data.get("report_id"),
                    "error": data.get("error"),
                },
                derived_slots={
                    key: value
                    for key, value in {
                        "fault_code": data.get("fault_code"),
                        "ecu_model": data.get("ecu_model"),
                    }.items()
                    if value
                },
                confidence=1.0,
            )

        if tool_name == "query_parameters":
            matched = bool(data.get("matched"))
            selected_source = data.get("selected_source") or {}
            rows = data.get("rows") or []
            first_row = rows[0] if rows else {}
            if matched:
                summary = data.get("summary") or f"参数命中：{selected_source.get('title') or '本地参数资料'}。"
            else:
                summary = data.get("message") or "参数查询未命中。"
            return CaseContextArtifact(
                artifact_id=f"ctx_{uuid4().hex}",
                type=CaseContextArtifactType.PARAMETER_RESULT,
                source_business="PARAM_QUERY",
                summary=summary,
                structured_data={
                    "status": status,
                    "matched": matched,
                    "query": data.get("query"),
                    "requested_field": data.get("requested_field"),
                    "requested_field_label": data.get("requested_field_label"),
                    "selected_source": {
                        "id": selected_source.get("id"),
                        "title": selected_source.get("title"),
                        "ecu_name": selected_source.get("ecu_name"),
                    },
                    "rows": [
                        {
                            "id": row.get("id"),
                            "ecu_pin_no": row.get("ecu_pin_no"),
                            "pin_definition": row.get("pin_definition"),
                            "requested_value": row.get("requested_value"),
                        }
                        for row in rows[:3]
                    ],
                },
                derived_slots={
                    key: value
                    for key, value in {
                        "ecu_model": selected_source.get("ecu_name"),
                        "parameter_source_id": selected_source.get("id"),
                    }.items()
                    if value
                },
                source_refs=list(data.get("source_refs") or [])[:3],
                confidence=1.0,
            )

        if tool_name == "lookup_repair_knowledge_titles":
            recommended = data.get("recommended_titles") or []
            top_titles = [item.get("title") for item in recommended[:3] if item.get("title")]
            summary = f"维修知识候选 {int(data.get('title_count') or 0)} 条"
            if top_titles:
                summary += f"：{', '.join(top_titles)}"
            return CaseContextArtifact(
                artifact_id=f"ctx_{uuid4().hex}",
                type=CaseContextArtifactType.REPAIR_KNOWLEDGE_RESULT,
                source_business="GENERAL_CHAT",
                summary=summary,
                structured_data={
                    "status": status,
                    "query": data.get("query"),
                    "title_count": data.get("title_count"),
                    "recommended_titles": [
                        {
                            "id": item.get("id"),
                            "title": item.get("title"),
                            "recall_score": item.get("recall_score"),
                        }
                        for item in recommended[:5]
                    ],
                },
                derived_slots={},
                confidence=0.9,
            )

        if tool_name == "get_repair_knowledge_context":
            primary = data.get("primary_source") or {}
            summary = f"已加载维修知识：{primary.get('title') or '已加载上下文'}"
            return CaseContextArtifact(
                artifact_id=f"ctx_{uuid4().hex}",
                type=CaseContextArtifactType.REPAIR_KNOWLEDGE_RESULT,
                source_business="GENERAL_CHAT",
                summary=summary,
                structured_data={
                    "status": status,
                    "loaded": data.get("loaded"),
                    "selected_entry_ids": data.get("selected_entry_ids") or [],
                    "primary_source": {
                        "id": primary.get("id"),
                        "title": primary.get("title"),
                    },
                },
                derived_slots={},
                source_refs=list(data.get("source_refs") or [])[:3],
                confidence=1.0,
            )

        return None

    def _apply_derived_slots(self, context: CaseContext, derived_slots: dict[str, Any]) -> None:
        for key, value in derived_slots.items():
            if value in (None, "", []):
                continue
            if key == "selected_doc_ids":
                context.slots.selected_doc_ids = [str(item) for item in value if item][: self._max_selected_docs]
                continue
            if key == "selected_doc_titles":
                context.slots.selected_doc_titles = [str(item) for item in value if item][: min(5, self._max_selected_docs)]
                continue
            if hasattr(context.slots, key):
                setattr(context.slots, key, value)

    @staticmethod
    def _derive_slots_from_selection_payload(selection_payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(selection_payload, dict):
            return {}

        derived = CaseContextManager._derive_slots_from_filters(selection_payload.get("filters"))
        file_ids = selection_payload.get("file_ids") or []
        if isinstance(file_ids, list) and file_ids:
            derived["selected_doc_ids"] = [str(item) for item in file_ids if item]
        return derived

    @staticmethod
    def _derive_slots_from_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(filters, dict):
            return {}
        derived: dict[str, Any] = {}
        for source_key, target_key in [
            ("brand", "brand"),
            ("series", "series"),
            ("model", "model"),
            ("platform", "platform"),
            ("engine", "engine"),
            ("emission", "emission"),
            ("doc_type", "doc_type"),
            ("fault_code", "fault_code"),
            ("subsystem", "subsystem"),
            ("ecu_model", "ecu_model"),
            ("param_source_id", "parameter_source_id"),
        ]:
            value = filters.get(source_key)
            if value not in (None, ""):
                derived[target_key] = value
        return derived

    @staticmethod
    def _derive_slots_from_image_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(evidence, dict):
            return {}
        vehicle = evidence.get("vehicle") if isinstance(evidence.get("vehicle"), dict) else {}
        diagnosis = evidence.get("diagnosis") if isinstance(evidence.get("diagnosis"), dict) else {}
        derived: dict[str, Any] = {}
        for source_key, target_key in [
            ("brand", "brand"),
            ("series", "series"),
            ("model", "model"),
            ("platform", "platform"),
            ("engine", "engine"),
            ("emission", "emission"),
        ]:
            value = vehicle.get(source_key)
            if value not in (None, "", []):
                derived[target_key] = value

        fault_codes = diagnosis.get("fault_codes") or []
        if isinstance(fault_codes, list) and fault_codes:
            derived["fault_code"] = str(fault_codes[0])
        ecu_model = diagnosis.get("ecu_model")
        if ecu_model not in (None, "", []):
            derived["ecu_model"] = ecu_model
        descriptions = diagnosis.get("descriptions") or []
        if isinstance(descriptions, list) and descriptions:
            derived["symptom"] = "；".join(str(item) for item in descriptions[:3] if item)
        return derived

    @staticmethod
    def _infer_image_evidence_business(evidence: dict[str, Any]) -> str:
        scene = str(evidence.get("scene") or "").strip()
        diagnosis = evidence.get("diagnosis") if isinstance(evidence.get("diagnosis"), dict) else {}
        vehicle = evidence.get("vehicle") if isinstance(evidence.get("vehicle"), dict) else {}
        if diagnosis.get("fault_codes"):
            return "FAULT_DIAGNOSIS"
        if scene == "diagnostic_screen":
            return "GENERAL_CHAT"
        if scene in {"vehicle_identity", "document_hint"} and any(
            vehicle.get(key) for key in ("brand", "series", "model", "engine", "emission")
        ):
            return "DOC_SEARCH"
        return "AGENT_LOOP"

    @staticmethod
    def _derive_doc_search_slots_from_ask_user(response: ChatResponse) -> dict[str, Any]:
        common_filters: dict[str, Any] | None = None
        for option in response.clarify_options:
            filters = (option.selection_payload or {}).get("filters") or {}
            if common_filters is None:
                common_filters = dict(filters)
                continue
            common_filters = {
                key: value
                for key, value in common_filters.items()
                if filters.get(key) == value
            }

        derived = CaseContextManager._derive_slots_from_filters(common_filters)
        ask_user_context = response.ask_user.context if response.ask_user is not None else {}
        top_result = ask_user_context.get("top_result") or {}
        if top_result.get("brand") and "brand" not in derived:
            derived["brand"] = top_result.get("brand")
        if top_result.get("series") and "series" not in derived:
            derived["series"] = top_result.get("series")
        if top_result.get("model") and "model" not in derived:
            derived["model"] = top_result.get("model")
        return derived

    def _refresh_budget_state(self, context: CaseContext) -> None:
        per_type = Counter(artifact.type.value for artifact in context.artifacts)
        context.budgets.artifact_count = len(context.artifacts)
        context.budgets.per_type = dict(per_type)
        context.budgets.serialized_bytes = len(context.model_dump_json().encode("utf-8"))

    def _refresh_working_state(self, context: CaseContext) -> None:
        context.task_type = self._resolve_task_type(context)
        context.missing_slots = self._derive_missing_slots(context)
        context.candidate_answer = self._derive_candidate_answer(context)
        context.answer_ready = (
            context.pending_action is None
            and context.candidate_answer is not None
            and not context.missing_slots
        )

    def _apply_loop_guard_state(self, context: CaseContext, loop_guard: Any | None) -> None:
        if loop_guard is None:
            return
        snapshot = loop_guard.snapshot()
        context.no_gain_streak = snapshot.no_gain_streak
        context.remaining_budget = CaseContextRemainingBudget(
            tool_calls_left=snapshot.remaining_tool_calls,
            external_calls_left=snapshot.remaining_external_tool_calls,
            ask_user_calls_left=snapshot.remaining_ask_user_calls,
        )

    @staticmethod
    def _resolve_task_type(context: CaseContext) -> str | None:
        if context.pending_action is not None and context.pending_action.business:
            return context.pending_action.business
        for artifact in reversed(context.artifacts):
            if artifact.source_business and artifact.source_business != "AGENT_LOOP":
                return artifact.source_business
        return context.task_type

    def _derive_missing_slots(self, context: CaseContext) -> list[str]:
        task_type = context.task_type
        if task_type == "PARAM_QUERY":
            latest_parameter = self._find_latest_artifact(context, CaseContextArtifactType.PARAMETER_RESULT)
            if latest_parameter is not None and bool(latest_parameter.structured_data.get("matched")):
                return []
            missing: list[str] = []
            if not context.slots.ecu_model:
                missing.append("ecu_model")
            if (
                context.pending_action is not None and context.pending_action.business == "PARAM_QUERY"
            ) or (
                latest_parameter is not None and latest_parameter.structured_data.get("status") == "need_clarify"
            ):
                if not context.slots.parameter_source_id:
                    missing.append("parameter_source_id")
            return missing

        if task_type == "FAULT_DIAGNOSIS":
            missing: list[str] = []
            if not context.slots.fault_code:
                missing.append("fault_code")
            latest_final_diag = self._find_latest_artifact(
                context,
                CaseContextArtifactType.DIAGNOSIS_RESULT,
                tool_name="dtc_diagnosis",
            )
            if latest_final_diag is None and not context.slots.ecu_model:
                missing.append("ecu_model")
            return missing

        if task_type == "DOC_SEARCH":
            if context.pending_action is None or context.pending_action.business != "DOC_SEARCH":
                return []
            missing: list[str] = []
            if not context.slots.brand:
                missing.append("brand")
            if not context.slots.series:
                missing.append("series")
            if not missing and not context.slots.model:
                missing.append("model")
            return missing

        if task_type == "GENERAL_CHAT":
            if context.pending_action is None or context.pending_action.business != "GENERAL_CHAT":
                return []
            missing: list[str] = []
            if not context.slots.fault_code:
                missing.append("fault_code")
            if not context.slots.symptom:
                missing.append("symptom")
            return missing or ["supplemental_information"]

        return []

    def _derive_candidate_answer(self, context: CaseContext) -> CaseContextCandidateAnswer | None:
        for artifact in reversed(context.artifacts):
            if artifact.type == CaseContextArtifactType.PARAMETER_RESULT and bool(
                artifact.structured_data.get("matched")
            ):
                return self._candidate_answer_from_artifact(artifact, source="query_parameters")

            if (
                artifact.type == CaseContextArtifactType.DIAGNOSIS_RESULT
                and artifact.structured_data.get("tool_name") == "dtc_diagnosis"
                and artifact.structured_data.get("status") != "failed"
            ):
                return self._candidate_answer_from_artifact(artifact, source="dtc_diagnosis")

            if (
                artifact.type == CaseContextArtifactType.DOC_SEARCH_RESULT
                and int(artifact.structured_data.get("returned_count") or 0) > 0
            ):
                return self._candidate_answer_from_artifact(artifact, source="doc_search")

            if (
                artifact.type == CaseContextArtifactType.REPAIR_KNOWLEDGE_RESULT
                and bool(artifact.structured_data.get("loaded"))
            ):
                return self._candidate_answer_from_artifact(artifact, source="repair_knowledge")

        return None

    @staticmethod
    def _candidate_answer_from_artifact(
        artifact: CaseContextArtifact,
        *,
        source: str,
    ) -> CaseContextCandidateAnswer:
        return CaseContextCandidateAnswer(
            business=artifact.source_business,
            summary=artifact.summary,
            source=source,
            confidence=artifact.confidence,
        )

    @staticmethod
    def _find_latest_artifact(
        context: CaseContext,
        artifact_type: CaseContextArtifactType,
        *,
        tool_name: str | None = None,
    ) -> CaseContextArtifact | None:
        for artifact in reversed(context.artifacts):
            if artifact.type != artifact_type:
                continue
            if tool_name is not None and artifact.structured_data.get("tool_name") != tool_name:
                continue
            return artifact
        return None

    @staticmethod
    def _default_attempted_action_summary(tool_name: str, envelope: dict[str, Any]) -> str:
        data = envelope.get("data") or {}
        message = data.get("message") or data.get("summary")
        if message:
            return str(message)
        status = str(envelope.get("status") or "ok")
        return f"{tool_name} 执行完成（{status}）"

    @staticmethod
    def _trim_dict(value: dict[str, Any], *, max_items: int) -> dict[str, Any]:
        items = list(value.items())[:max_items]
        trimmed: dict[str, Any] = {}
        for key, item in items:
            if isinstance(item, list):
                trimmed[key] = item[:3]
            elif isinstance(item, dict):
                trimmed[key] = dict(list(item.items())[:6])
            else:
                trimmed[key] = item
        return trimmed

    @staticmethod
    def _stringify_answer(answer: Any) -> str:
        text = str(answer)
        return text if len(text) <= 120 else f"{text[:117]}..."
