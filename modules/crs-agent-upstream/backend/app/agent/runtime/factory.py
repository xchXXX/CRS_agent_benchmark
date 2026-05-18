"""Pydantic AI runtime factory."""

from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from app.agent.model_ids import normalize_configured_model
from app.agent.adapters.legacy_fault_diag_adapter import LegacyFaultDiagAdapter
from app.agent.context.manager import CaseContextManager
from app.agent.domain.fault_diagnosis.review import review_fault_diagnosis_execution
from app.agent.domain.parameter_query.response_adapter import (
    PARAM_QUERY_DEFERRED_TOOL_NAME,
    ParameterQueryResponseAdapter,
)
from app.agent.domain.repair_knowledge.rendering import RepairRenderPlan
from app.agent.domain.parameter_query.review import review_parameter_query_execution
from app.agent.models.ask_user import AskUserInputType, AskUserOption
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.tools.base import ToolExecutionMode
from app.core.config import Settings, settings as app_settings


@dataclass(frozen=True)
class AgentFactoryStatus:
    available: bool
    reason: str
    version: str | None = None


class AgentFactory:
    """Factory for the current Pydantic AI runtime."""

    def __init__(
        self,
        settings: Settings | None = None,
        model_override: Any | None = None,
        gate_model_override: Any | None = None,
        renderer_model_override: Any | None = None,
    ):
        self._settings = settings or app_settings
        self._model_override = model_override
        self._gate_model_override = gate_model_override
        self._renderer_model_override = renderer_model_override

    def get_status(self) -> AgentFactoryStatus:
        try:
            import_module("pydantic_ai")
        except Exception as exc:
            reason = f"Failed to import pydantic_ai: {exc}"
            return AgentFactoryStatus(available=False, reason=reason)

        try:
            installed_version = version("pydantic_ai_slim")
        except PackageNotFoundError:
            installed_version = None

        reason = "pydantic_ai is available."
        return AgentFactoryStatus(available=True, reason=reason, version=installed_version)

    def is_available(self) -> bool:
        return self.get_status().available

    def create_agent(self, deps: AgentRuntimeDeps):
        return self._create_agent_with_tools(
            name="crs_agent_loop",
            instructions=self._get_runtime_text_config(
                deps,
                "agent_system_prompt",
                self._settings.agent_system_prompt,
            ),
            output_type_mode="deferred_or_text",
            model_override=self._model_override,
            include_ask_user=True,
            deps=deps,
        )

    def create_repair_gate_agent(self, deps: AgentRuntimeDeps):
        base_prompt = self._get_runtime_text_config(
            deps,
            "agent_system_prompt",
            self._settings.agent_system_prompt,
        )
        return self._create_agent_with_tools(
            name="crs_repair_pre_answer_gate",
            instructions=(
                f"{base_prompt} "
                "你是维修问答的答前审查器。"
                "你的唯一任务是判断当前轮次是否已经可以直接回答，还是必须先继续向用户补充信息。"
                "绝对不要输出面向用户的最终回答正文。"
                "你需要先使用维修知识相关工具检查本地维修资料，如果能命中优先根据维修经验来判断，如果出现维修经验没有涉及的你可以从一个汽修专家角度自行分析。"
                "如果仍缺关键资料，下一步必须调用 `ask_user_question`来询问你缺少的信息。"
                "如果问题属于启动/起动/打不着火/起动机无反应，生成 `ask_user_question` 时必须尽量给出可点选的预测候选项，尤其是现象、工况和报码方向，不要把这些字段留成空列表。"
                "判断“现有信息已经足够回答”的标准，不是能输出一段看起来完整的话，而是已经足够把问题收敛到 1 到 3 个高概率方向，并给出第一步有针对性的检查动作。"
                "如果基于现有信息，你只能给出任何车辆都适用的泛化排查建议，这不算信息足够，必须继续调用 `ask_user_question`。"
                "如果用户已经补充过信息，你必须检查这些补充是否真的改变了诊断优先级；如果仍然不能明显改变优先级，说明补充信息还不够关键，不能直接放行。"
                "只有在你已经能够把问题先分型，再给出按顺序的诊断路径、关键判断依据和维修处理闭环时，才允许放行。"
                "如果你只能罗列可能原因，或者只能给出一组泛化检查建议，但还不能说明先查什么、查到什么算异常、下一步往哪里走，就不允许放行。"
                "对于轨压低、通讯故障、启动困难、动力不足这类问题，必须先判断当前属于哪一种工况或故障类型；如果当前还不能稳定分型，就继续 ask user。"
                "对于启动/起动机/打不着火/只有咔哒声这类问题，优先判断是否已经掌握以下大部分关键线索：是否久放、蓄电池状态、起动时仪表或灯光是否明显变暗、是单次咔哒还是连续咔哒、起动机是否完全不转、是否报码、是否做过搭电或更换电瓶、是否有明显接线松动或发热。"
                "如果这些关键线索还缺得比较多，不允许直接回答。"
                "如果这是用户补充信息后的恢复轮次，你必须优先读取 prompt 中明确列出的“已回答字段”和“已回答内容”。"
                "这些字段一旦已经回答，就禁止再次用 ask_user_question 重复问同一字段或同义字段。"
                "如果还需要继续追问，只能问新的、尚未回答、且信息增益更高的字段。"
                "绝对不要因为仍想确认一遍，就把已经回答过的故障码、现象、工况再次问一轮。"
                "如果现有信息已经足够回答，就只输出 `__READY_TO_ANSWER__`，不要输出任何其他内容。"
                "不要输出“由于缺乏针对性的维修案例”“为了更精准地协助您”这类面向用户的话术。"
                "不要输出 markdown、解释或中间推理。"
            ),
            output_type_mode="deferred_or_text",
            model_override=self._gate_model_override or self._model_override,
            include_ask_user=True,
            enabled_tools={
                "lookup_repair_knowledge_titles",
                "get_repair_knowledge_context",
            },
            deps=deps,
        )

    def create_repair_renderer_agent(self, deps: AgentRuntimeDeps):
        base_prompt = self._get_runtime_text_config(
            deps,
            "agent_system_prompt",
            self._settings.agent_system_prompt,
        )
        return self._create_agent_with_tools(
            name="crs_repair_answer_renderer",
            instructions=(
                f"{base_prompt} "
                "你是维修问答的最终答案渲染器，回答前审查已经确认现有信息足够。"
                "你的任务不是自己决定回答模板，而是严格执行 user_prompt 里给出的回答框架、重点和禁区。"
                "你要像一线维修老师傅一样回答，不要像教材、客服或培训资料。"
                "第一句话必须自然以“老哥，”开头，整段回答里自然使用 1 到 3 次“老哥”即可。"
                "不要再向用户索取信息，不要调用 `ask_user_question`，也不要输出内部推理。"
                "禁止写“由于缺乏针对性的维修案例”“当前证据不足”“为了更精准地协助您，请提供更多信息”这类话术。"
                "如果 user_prompt 指定这是参数、原理、位置或操作类回答，就不要强行改写成故障诊断模板。"
                "如果 user_prompt 指定这是诊断类回答，就必须写出先后顺序、判断依据、异常分支、处理动作和必要的复验。"
                "不要只给结论，也不要只写可能原因分类。"
                "能给参数或阈值时直接写；拿不到可靠参数时，也要写清楚正常表现和异常表现。"
                "默认把答案写成师傅能照着干的现场步骤，不要写成报告摘要。"
            ),
            output_type_mode="text_only",
            model_override=self._renderer_model_override or self._model_override,
            include_ask_user=False,
            enabled_tools={
                "lookup_ecu_candidates",
                "dtc_diagnosis",
                "lookup_repair_knowledge_titles",
                "get_repair_knowledge_context",
                "query_parameters",
            },
            deps=deps,
        )

    def create_repair_render_planner_agent(self, deps: AgentRuntimeDeps):
        base_prompt = self._get_runtime_text_config(
            deps,
            "agent_system_prompt",
            self._settings.agent_system_prompt,
        )
        return self._create_agent_with_tools(
            name="crs_repair_render_planner",
            instructions=(
                f"{base_prompt} "
                "你是维修问答的最终回答规划器。"
                "你的任务是从 `dtc_diagnosis`、`symptom_diagnosis`、`spec_answer`、`principle_explanation`、`location_identification`、`operation_guide` 里选择最合适的一个回答框架。"
                "只输出结构化的 RepairRenderPlan，不要输出面向用户的正文。"
                "你要基于当前问题、用户补充信息、已加载维修资料和工具结果来选择框架。"
                "不要默认所有问题都走故障诊断模板。"
                "报码类优先考虑 `dtc_diagnosis`；故障现象和排查类优先考虑 `symptom_diagnosis`；"
                "参数或标准值类优先考虑 `spec_answer`；原理类优先考虑 `principle_explanation`；"
                "位置、接口、区分类优先考虑 `location_identification`；使用、年检、操作步骤类优先考虑 `operation_guide`。"
                "如果当前问题明显属于参数、原理、位置或操作类，不要再规划成症状诊断。"
                "除了 frame 以外，你还要决定答案深度 `answer_depth`，以及 required_elements、optional_elements、min_steps、need_thresholds、need_branching、need_recheck。"
                "不是所有问题都答成重型工单。能直接回答的就 direct；需要师傅按步骤排的用 standard；涉及通讯、供电、复杂症状或补充信息已经较完整时用 playbook。"
                "诊断类答案默认要求 branching=true；参数类默认 need_thresholds=true；操作类默认要有 min_steps 和 need_recheck。"
                "focus_points 只保留 1 到 4 条真正会影响最终写法的重点。"
                "forbid_followup_text 必须为 true。"
            ),
            output_type_override=RepairRenderPlan,
            model_override=self._renderer_model_override or self._model_override,
            include_ask_user=False,
            enabled_tools=set(),
            deps=deps,
        )

    def _create_agent_with_tools(
        self,
        *,
        name: str,
        instructions: str,
        output_type_mode: str = "deferred_or_text",
        output_type_override: Any | None = None,
        model_override: Any | None,
        include_ask_user: bool,
        enabled_tools: set[str] | None = None,
        deps: AgentRuntimeDeps,
    ):
        status = self.get_status()
        if not status.available:
            raise RuntimeError(status.reason)

        from pydantic_ai import Agent, DeferredToolRequests, RunContext
        from pydantic_ai.exceptions import CallDeferred
        from pydantic_ai.models.test import TestModel

        model = self._build_model(TestModel, deps=deps, override=model_override)
        output_type: Any
        if output_type_override is not None:
            output_type = output_type_override
        elif output_type_mode == "text_only":
            output_type = str
        else:
            output_type = [str, DeferredToolRequests]

        agent = Agent(
            model=model,
            name=name,
            deps_type=AgentRuntimeDeps,
            output_type=output_type,
            instructions=instructions,
            retries=2,
            output_retries=2,
            defer_model_check=True,
        )

        allowed_tools = (
            set(enabled_tools)
            if enabled_tools is not None
            else {
                "lookup_ecu_candidates",
                "dtc_diagnosis",
                "lookup_repair_knowledge_titles",
                "get_repair_knowledge_context",
                "query_parameters",
            }
        )

        if include_ask_user:
            @agent.tool
            async def ask_user_question(
                ctx: RunContext[AgentRuntimeDeps],
                question: str,
                input_type: AskUserInputType = AskUserInputType.TEXT,
                options: list[AskUserOption] | None = None,
                allow_free_input: bool = False,
                input_hint: str | None = None,
                unit: str | None = None,
                reference_range: str | None = None,
                context: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                self._guard_tool_call(ctx.deps, "ask_user_question", {"question": question, "context": context or {}})
                raise CallDeferred(
                    metadata={
                        "question": question,
                        "input_type": input_type.value,
                        "options": [self._serialize_option(option) for option in (options or [])],
                        "allow_free_input": allow_free_input,
                        "input_hint": input_hint,
                        "unit": unit,
                        "reference_range": reference_range,
                        "context": context or {},
                    }
                )

        if "lookup_ecu_candidates" in allowed_tools:
            @agent.tool
            async def lookup_ecu_candidates(
                ctx: RunContext[AgentRuntimeDeps],
                fault_code: str,
            ) -> dict[str, Any]:
                self._guard_tool_call(ctx.deps, "lookup_ecu_candidates", {"fault_code": fault_code})
                result = await LegacyFaultDiagAdapter(ctx.deps).lookup_ecu_candidates(fault_code)
                self._record_tool_result(ctx.deps, "lookup_ecu_candidates", {"fault_code": fault_code}, result)
                return result

        if "dtc_diagnosis" in allowed_tools:
            @agent.tool
            async def dtc_diagnosis(
                ctx: RunContext[AgentRuntimeDeps],
                fault_code: str,
                ecu_model: str,
            ) -> dict[str, Any]:
                self._guard_tool_call(
                    ctx.deps,
                    "dtc_diagnosis",
                    {"fault_code": fault_code, "ecu_model": ecu_model},
                )
                review = review_fault_diagnosis_execution(
                    case_context=ctx.deps.case_context,
                    runtime_tool_history=ctx.deps.runtime_tool_history,
                    fault_code=fault_code,
                    ecu_model=ecu_model,
                )
                if review.blocked and review.envelope is not None:
                    self._record_tool_result(
                        ctx.deps,
                        "dtc_diagnosis",
                        {"fault_code": fault_code, "ecu_model": ecu_model},
                        review.envelope,
                    )
                    tracer = getattr(ctx.deps, "tracer", None)
                    if tracer is not None:
                        tracer.trace(
                            event_type="fault_diagnosis_review_blocked",
                            session_id=getattr(ctx.deps, "request_session_id", None),
                            payload={
                                "fault_code": fault_code,
                                "ecu_model": ecu_model,
                                "reason": review.reason,
                            },
                        )
                    return review.envelope
                result = await LegacyFaultDiagAdapter(ctx.deps).diagnose(fault_code=fault_code, ecu_model=ecu_model)
                self._record_tool_result(
                    ctx.deps,
                    "dtc_diagnosis",
                    {"fault_code": fault_code, "ecu_model": ecu_model},
                    result,
                )
                return result

        if "lookup_repair_knowledge_titles" in allowed_tools:
            @agent.tool
            async def lookup_repair_knowledge_titles(
                ctx: RunContext[AgentRuntimeDeps],
                query: str,
            ) -> dict[str, Any]:
                self._guard_tool_call(ctx.deps, "lookup_repair_knowledge_titles", {"query": query})
                service = ctx.deps.repair_knowledge_service
                if service is None:
                    result = {
                        "status": "ok",
                        "data": {
                            "query": query,
                            "decision_mode": "llm_must_decide_match",
                            "title_count": 0,
                            "recommended_titles": [],
                            "titles": [],
                            "guidance": "Local repair knowledge is unavailable.",
                        },
                    }
                    self._record_tool_result(ctx.deps, "lookup_repair_knowledge_titles", {"query": query}, result)
                    return result
                result = service.lookup_titles(query=query)
                self._record_tool_result(ctx.deps, "lookup_repair_knowledge_titles", {"query": query}, result)
                return result

        if "get_repair_knowledge_context" in allowed_tools:
            @agent.tool
            async def get_repair_knowledge_context(
                ctx: RunContext[AgentRuntimeDeps],
                entry_ids: list[str],
            ) -> dict[str, Any]:
                self._guard_tool_call(ctx.deps, "get_repair_knowledge_context", {"entry_ids": entry_ids})
                service = ctx.deps.repair_knowledge_service
                if service is None:
                    result = {
                        "status": "ok",
                        "data": {
                            "loaded": False,
                            "entries": [],
                            "source_refs": [],
                            "primary_source": None,
                            "llm_context": "",
                        },
                    }
                    self._record_tool_result(
                        ctx.deps,
                        "get_repair_knowledge_context",
                        {"entry_ids": entry_ids},
                        result,
                    )
                    return result
                result = service.load_context(entry_ids=entry_ids)
                self._record_tool_result(
                    ctx.deps,
                    "get_repair_knowledge_context",
                    {"entry_ids": entry_ids},
                    result,
                )
                return result

        if "query_parameters" in allowed_tools:
            @agent.tool
            async def query_parameters(
                ctx: RunContext[AgentRuntimeDeps],
                query: str,
                selection_payload: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                effective_query = CaseContextManager.build_parameter_query_with_context(ctx.deps.case_context, query)
                effective_selection_payload = CaseContextManager.build_parameter_selection_payload(
                    ctx.deps.case_context,
                    selection_payload,
                )
                self._guard_tool_call(
                    ctx.deps,
                    "query_parameters",
                    {"query": effective_query, "selection_payload": effective_selection_payload or {}},
                )
                review = review_parameter_query_execution(
                    case_context=ctx.deps.case_context,
                    runtime_tool_history=ctx.deps.runtime_tool_history,
                    query=effective_query,
                    selection_payload=effective_selection_payload,
                )
                if review.blocked and review.envelope is not None:
                    self._record_tool_result(
                        ctx.deps,
                        "query_parameters",
                        {"query": effective_query, "selection_payload": effective_selection_payload or {}},
                        review.envelope,
                    )
                    tracer = getattr(ctx.deps, "tracer", None)
                    if tracer is not None:
                        tracer.trace(
                            event_type="parameter_query_review_blocked",
                            session_id=getattr(ctx.deps, "request_session_id", None),
                            payload={
                                "query": effective_query,
                                "selection_payload": effective_selection_payload or {},
                                "reason": review.reason,
                            },
                        )
                    result = review.envelope
                else:
                    service = ctx.deps.parameter_query_service
                    if service is None:
                        result = {
                            "status": "failed",
                            "data": {"message": "parameter_query_service is unavailable."},
                        }
                        self._record_tool_result(
                            ctx.deps,
                            "query_parameters",
                            {"query": effective_query, "selection_payload": effective_selection_payload or {}},
                            result,
                        )
                        return result
                    result = await service.query_async(
                        query=effective_query,
                        selection_payload=effective_selection_payload,
                        raw_query=query,
                    )
                    self._record_tool_result(
                        ctx.deps,
                        "query_parameters",
                        {"query": effective_query, "selection_payload": effective_selection_payload or {}},
                        result,
                    )

                if str(result.get("status") or "").strip().lower() == "need_clarify":
                    ask_user = ParameterQueryResponseAdapter.build_ask_user_question(result)
                    raise CallDeferred(
                        metadata={
                            "deferred_as": "ask_user_question",
                            "deferred_tool_name": PARAM_QUERY_DEFERRED_TOOL_NAME,
                            "question": ask_user.question,
                            "input_type": ask_user.input_type.value,
                            "options": [self._serialize_option(option) for option in ask_user.options],
                            "allow_free_input": ask_user.allow_free_input,
                            "input_hint": ask_user.input_hint,
                            "unit": ask_user.unit,
                            "reference_range": ask_user.reference_range,
                            "context": ask_user.context or {},
                            "query": effective_query,
                        }
                    )
                return result

        return agent

    def _build_model(
        self,
        test_model_cls: Any,
        *,
        deps: AgentRuntimeDeps,
        override: Any | None = None,
    ) -> Any:
        if override is not None:
            return override
        agent_model = str(
            self._get_runtime_config_value(
                deps,
                "agent_model",
                self._settings.agent_model,
            )
            or self._settings.agent_model
        ).strip()
        if agent_model == "test":
            return test_model_cls(
                call_tools=self._settings.agent_test_call_tools_list or [],
                custom_output_text=self._settings.agent_test_output_text,
                model_name="crs-test",
            )

        return normalize_configured_model(agent_model)

    def _get_runtime_config_value(
        self,
        deps: AgentRuntimeDeps,
        key: str,
        default: Any,
    ) -> Any:
        config_service = getattr(deps, "config_service", None)
        if config_service is None:
            return default
        return config_service.get(key, default)

    def _get_runtime_text_config(
        self,
        deps: AgentRuntimeDeps,
        key: str,
        default: str,
    ) -> str:
        value = self._get_runtime_config_value(deps, key, default)
        text = str(value or "").strip()
        return text or default

    @staticmethod
    def _serialize_option(option: AskUserOption | dict[str, Any]) -> dict[str, Any]:
        if isinstance(option, AskUserOption):
            return option.model_dump(mode="json")

        return AskUserOption.model_validate(option).model_dump(mode="json")

    @classmethod
    def _guard_tool_call(cls, deps: AgentRuntimeDeps, tool_name: str, args: dict[str, Any]) -> None:
        guard = getattr(deps, "loop_guard", None)
        if guard is None:
            return
        is_external, is_user_interaction, category = cls._tool_flags(deps, tool_name)
        try:
            guard.before_tool_call(
                tool_name,
                args,
                is_external=is_external,
                is_user_interaction=is_user_interaction,
            )
        except Exception as exc:
            cls._trace_guard_snapshot(
                deps=deps,
                event_type="agent_loop_guard_blocked_before_tool_call",
                tool_name=tool_name,
                extra_payload={
                    "args": args,
                    "tool_category": category,
                    "error": str(exc),
                },
            )
            raise
        cls._trace_guard_snapshot(
            deps=deps,
            event_type="agent_loop_guard_before_tool_call",
            tool_name=tool_name,
            extra_payload={"args": args, "tool_category": category},
        )

    @classmethod
    def _record_tool_result(
        cls,
        deps: AgentRuntimeDeps,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any] | Any,
    ) -> None:
        guard = getattr(deps, "loop_guard", None)
        if guard is not None:
            try:
                info_gain = guard.after_tool_call(tool_name, result)
            except Exception as exc:
                cls._trace_guard_snapshot(
                    deps=deps,
                    event_type="agent_loop_guard_blocked_after_tool_call",
                    tool_name=tool_name,
                    extra_payload={"error": str(exc)},
                )
                raise
            cls._trace_guard_snapshot(
                deps=deps,
                event_type="agent_loop_guard_after_tool_call",
                tool_name=tool_name,
                extra_payload={"info_gain": info_gain},
            )

        runtime_tool_history = getattr(deps, "runtime_tool_history", None)
        if isinstance(runtime_tool_history, list):
            runtime_tool_history.append(
                {
                    "tool_name": tool_name,
                    "args": dict(args or {}),
                    "result": result,
                }
            )

    @staticmethod
    def _tool_flags(deps: AgentRuntimeDeps, tool_name: str) -> tuple[bool, bool, str]:
        spec = deps.tool_registry.get(tool_name) if deps.tool_registry is not None else None
        tags = set(spec.tags or []) if spec is not None else set()
        is_user_interaction = (
            spec is not None and spec.execution_mode == ToolExecutionMode.DEFERRED
        ) or "interaction" in tags or "ask_user" in tags
        is_external = "external" in tags
        if is_user_interaction:
            return is_external, True, "interaction"
        if is_external:
            return True, False, "external"
        return False, False, "local"

    @staticmethod
    def _trace_guard_snapshot(
        *,
        deps: AgentRuntimeDeps,
        event_type: str,
        tool_name: str,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        guard = getattr(deps, "loop_guard", None)
        tracer = getattr(deps, "tracer", None)
        if guard is None or tracer is None:
            return
        snapshot = guard.snapshot()
        payload = {
            "tool_name": tool_name,
            "budget": snapshot.__dict__,
        }
        if extra_payload:
            payload.update(extra_payload)
        tracer.trace(
            event_type=event_type,
            session_id=getattr(deps, "request_session_id", None),
            payload=payload,
        )
