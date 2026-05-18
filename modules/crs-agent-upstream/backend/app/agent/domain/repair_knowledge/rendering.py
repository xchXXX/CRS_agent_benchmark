"""Planning, strategy, and review helpers for repair answer rendering."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.adapters.repair_knowledge_followup_adapter import RepairKnowledgeFollowupAdapter


FAULT_CODE_PATTERN = re.compile(r"\b[PBCU][0-9A-F]{4}\b", re.IGNORECASE)
SHORT_CODE_QUERY_PATTERN = re.compile(r"^[A-Z0-9]{3,8}$", re.IGNORECASE)
DIAGNOSIS_TEMPLATE_HEADINGS = (
    "### 故障定义",
    "### 当前更像哪一型",
    "### 可能原因分类",
    "### 分步检查",
    "### 判断依据",
    "### 维修处理",
    "### 易误判点",
)
SPEC_HINTS = (
    "多少",
    "几伏",
    "几欧",
    "电压",
    "电阻",
    "阻值",
    "开度",
    "针脚",
    "标准数据流",
    "测试数据",
    "报码含义",
)
PRINCIPLE_HINTS = (
    "原理",
    "工作原理",
    "控制原理",
    "启动原理",
    "逻辑",
)
LOCATION_HINTS = (
    "在哪",
    "在哪里",
    "位置",
    "检测口",
    "诊断口",
    "接口",
    "怎么区分",
    "如何区分",
    "怎么分辨",
    "如何分辨",
)
OPERATION_HINTS = (
    "如何使用",
    "怎么使用",
    "怎么用",
    "如何操作",
    "操作步骤",
    "未就绪",
    "接v3",
    "刷写",
    "年检",
    "怎么做年检",
)
SPEC_VALUE_HINTS = (
    "v",
    "伏",
    "欧",
    "ohm",
    "%",
    "bar",
    "kpa",
)


class RepairAnswerFrame(str, Enum):
    DTC_DIAGNOSIS = "dtc_diagnosis"
    SYMPTOM_DIAGNOSIS = "symptom_diagnosis"
    SPEC_ANSWER = "spec_answer"
    PRINCIPLE_EXPLANATION = "principle_explanation"
    LOCATION_IDENTIFICATION = "location_identification"
    OPERATION_GUIDE = "operation_guide"


class RepairAnswerDepth(str, Enum):
    DIRECT = "direct"
    STANDARD = "standard"
    PLAYBOOK = "playbook"


class RepairRenderContext(BaseModel):
    query: str
    normalized_query: str
    summary_text: str = ""
    flattened_fields: str = ""
    loaded_context: dict[str, Any] = Field(default_factory=dict)
    source_titles: list[str] = Field(default_factory=list)
    source_count: int = 0
    has_followup: bool = False
    has_fault_code: bool = False
    has_principle_signal: bool = False
    has_spec_signal: bool = False
    has_location_signal: bool = False
    has_operation_signal: bool = False
    has_symptom_signal: bool = False


class RepairRenderPlan(BaseModel):
    frame: RepairAnswerFrame
    response_goal: str = Field(min_length=4, max_length=160)
    confidence: Literal["low", "medium", "high"] = "medium"
    answer_depth: RepairAnswerDepth = RepairAnswerDepth.STANDARD
    required_elements: list[str] = Field(default_factory=list, max_length=8)
    optional_elements: list[str] = Field(default_factory=list, max_length=8)
    min_steps: int = Field(default=0, ge=0, le=8)
    need_thresholds: bool = False
    need_branching: bool = False
    need_recheck: bool = False
    focus_points: list[str] = Field(default_factory=list, max_length=4)
    keep_mechanic_tone: bool = True
    forbid_followup_text: bool = True


class RepairRenderedReview(BaseModel):
    accepted: bool
    content: str
    reasons: list[str] = Field(default_factory=list)


def build_repair_render_context(
    *,
    query: str,
    summary_text: str = "",
    flattened_fields: str = "",
    loaded_context: dict[str, Any] | None = None,
) -> RepairRenderContext:
    normalized_query = RepairKnowledgeFollowupAdapter.normalize_query_text(query)
    lowered = normalized_query.lower()
    sources = list((loaded_context or {}).get("source_refs") or [])
    source_titles = [
        str(item.get("title") or "").strip()
        for item in sources
        if str(item.get("title") or "").strip()
    ][:4]
    combined_text = "\n".join(part for part in [normalized_query, summary_text, flattened_fields] if part).strip()
    lowered_combined = combined_text.lower()
    return RepairRenderContext(
        query=query.strip(),
        normalized_query=normalized_query,
        summary_text=summary_text.strip(),
        flattened_fields=flattened_fields.strip(),
        loaded_context=loaded_context or {},
        source_titles=source_titles,
        source_count=len(sources),
        has_followup=bool(summary_text.strip() or flattened_fields.strip()),
        has_fault_code=bool(FAULT_CODE_PATTERN.search(normalized_query) or SHORT_CODE_QUERY_PATTERN.fullmatch(normalized_query)),
        has_principle_signal=any(hint in normalized_query for hint in PRINCIPLE_HINTS),
        has_spec_signal=(
            any(hint in normalized_query for hint in SPEC_HINTS)
            or any(hint in lowered for hint in SPEC_VALUE_HINTS)
        ),
        has_location_signal=any(hint in normalized_query for hint in LOCATION_HINTS),
        has_operation_signal=any(hint in lowered for hint in OPERATION_HINTS),
        has_symptom_signal=RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query(combined_text),
    )


def default_repair_render_plan(context: RepairRenderContext) -> RepairRenderPlan:
    frame = _default_frame_for_context(context)
    goal_map = {
        RepairAnswerFrame.DTC_DIAGNOSIS: "围绕报码方向、先查什么、异常后怎么走和修完如何确认给出闭环诊断。",
        RepairAnswerFrame.SYMPTOM_DIAGNOSIS: "把故障现象收敛成可执行的排查路径，明确先查什么、怎么判断、异常后往哪走。",
        RepairAnswerFrame.SPEC_ANSWER: "直接给出参数或标准值，并说明适用前提与现场核对方式。",
        RepairAnswerFrame.PRINCIPLE_EXPLANATION: "把系统原理讲清楚，并说明这些原理如何指导现场判断。",
        RepairAnswerFrame.LOCATION_IDENTIFICATION: "帮助用户定位接口、部件或区分方式，并给出现实中的确认方法。",
        RepairAnswerFrame.OPERATION_GUIDE: "按使用场景给出步骤化操作指导，并说明成功判据和注意事项。",
    }
    answer_depth = _default_depth_for_context(frame=frame, context=context)
    confidence = "high" if _has_strong_frame_signal(frame, context) else "medium"
    focus_points = _build_focus_points(frame=frame, context=context)
    required_elements, optional_elements = _build_required_elements(frame=frame, depth=answer_depth)
    return RepairRenderPlan(
        frame=frame,
        response_goal=goal_map[frame],
        confidence=confidence,
        answer_depth=answer_depth,
        required_elements=required_elements,
        optional_elements=optional_elements,
        min_steps=_default_min_steps(frame=frame, depth=answer_depth),
        need_thresholds=_default_need_thresholds(frame=frame, context=context),
        need_branching=_default_need_branching(frame=frame),
        need_recheck=_default_need_recheck(frame=frame, depth=answer_depth),
        focus_points=focus_points,
    )


def validate_repair_render_plan(
    plan: RepairRenderPlan,
    *,
    context: RepairRenderContext,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not str(plan.response_goal or "").strip():
        reasons.append("missing_response_goal")
    if not plan.required_elements:
        reasons.append("missing_required_elements")
    if plan.min_steps < 0:
        reasons.append("invalid_min_steps")

    if context.has_fault_code and plan.frame in {
        RepairAnswerFrame.LOCATION_IDENTIFICATION,
        RepairAnswerFrame.PRINCIPLE_EXPLANATION,
    }:
        reasons.append("fault_code_query_misframed")
    if context.has_principle_signal and plan.frame not in {
        RepairAnswerFrame.PRINCIPLE_EXPLANATION,
        RepairAnswerFrame.OPERATION_GUIDE,
    }:
        reasons.append("principle_query_misframed")
    if context.has_location_signal and plan.frame not in {
        RepairAnswerFrame.LOCATION_IDENTIFICATION,
        RepairAnswerFrame.SPEC_ANSWER,
    }:
        reasons.append("location_query_misframed")
    if context.has_operation_signal and plan.frame not in {
        RepairAnswerFrame.OPERATION_GUIDE,
        RepairAnswerFrame.PRINCIPLE_EXPLANATION,
    }:
        reasons.append("operation_query_misframed")
    if context.has_symptom_signal and plan.frame == RepairAnswerFrame.SPEC_ANSWER:
        reasons.append("symptom_query_misframed_as_spec")
    if context.has_spec_signal and not context.has_symptom_signal and plan.frame == RepairAnswerFrame.SYMPTOM_DIAGNOSIS:
        reasons.append("spec_query_misframed")
    if plan.frame in {RepairAnswerFrame.DTC_DIAGNOSIS, RepairAnswerFrame.SYMPTOM_DIAGNOSIS}:
        if not plan.need_branching:
            reasons.append("diagnosis_missing_branching")
        if plan.min_steps < 2:
            reasons.append("diagnosis_steps_too_few")
    if plan.frame == RepairAnswerFrame.SPEC_ANSWER and not plan.need_thresholds:
        reasons.append("spec_answer_missing_threshold_contract")
    if plan.frame == RepairAnswerFrame.OPERATION_GUIDE and plan.min_steps < 2:
        reasons.append("operation_steps_too_few")
    return not reasons, reasons


class RepairRenderStrategy(ABC):
    frame: RepairAnswerFrame

    def build_prompt(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        prompt_parts = [
            "请基于消息历史里已经加载的资料、工具结果和共享上下文，直接输出最终回答。",
            "请直接回答用户当前问题，不要输出中间推理。",
            f"当前问题：{context.query}",
        ]
        if context.summary_text:
            prompt_parts.append(f"用户补充摘要：{context.summary_text}")
        if context.flattened_fields:
            prompt_parts.append(f"结构化补充：{context.flattened_fields}")
        if context.source_titles:
            prompt_parts.append(f"已加载资料：{'；'.join(context.source_titles)}")
        evidence_summary = self._structured_evidence_summary(context)
        if evidence_summary:
            prompt_parts.append(f"结构化证据：{evidence_summary}")
        prompt_parts.extend(
            [
                f"回答框架：{plan.frame.value}",
                f"回答目标：{plan.response_goal}",
                f"回答深度：{plan.answer_depth.value}",
                f"最少步骤数：{plan.min_steps}",
                f"必带元素：{'；'.join(plan.required_elements) if plan.required_elements else '无'}",
                f"可选增强：{'；'.join(plan.optional_elements) if plan.optional_elements else '无'}",
                f"阈值/正常表现：{'必须写' if plan.need_thresholds else '有可靠信息就写'}",
                f"分支判断：{'必须写' if plan.need_branching else '按需写'}",
                f"复验：{'必须写' if plan.need_recheck else '有必要就写'}",
                f"重点关注：{'；'.join(plan.focus_points) if plan.focus_points else '结合用户现有补充和已加载资料'}",
                "统一要求：第一节正文第一句必须以“老哥，”开头；不要再向用户索取信息；不要调用 ask_user_question；不要写元话术、资料不足、案例不足、为了更精准请补充信息之类的话。",
                self._frame_contract(plan=plan, context=context),
            ]
        )
        return "\n".join(part for part in prompt_parts if part).strip()

    def build_retry_prompt(
        self,
        *,
        plan: RepairRenderPlan,
        context: RepairRenderContext,
        previous_answer: str,
        reasons: list[str],
    ) -> str:
        parts = [
            self.build_prompt(plan=plan, context=context),
            "上一版回答没有满足最终输出要求，请直接重写完整答案。",
            f"未达标原因：{'；'.join(reasons) if reasons else '未通过结构审查'}",
            "修正要求：不要解释修改原因；不要摘要式重写；必须把步骤、判据、分支和处理动作写完整。",
            f"上一版回答：\n{previous_answer.strip()}",
        ]
        return "\n".join(part for part in parts if part).strip()

    def review(self, *, content: str, plan: RepairRenderPlan, context: RepairRenderContext) -> RepairRenderedReview:
        normalized = RepairKnowledgeFollowupAdapter.normalize_user_facing_message(content)
        reasons = self._review_rules(content=normalized, plan=plan, context=context)
        return RepairRenderedReview(
            accepted=not reasons and bool(normalized.strip()),
            content=normalized.strip(),
            reasons=reasons,
        )

    @abstractmethod
    def fallback(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        raise NotImplementedError

    @abstractmethod
    def _frame_contract(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        raise NotImplementedError

    @abstractmethod
    def _review_rules(
        self,
        *,
        content: str,
        plan: RepairRenderPlan,
        context: RepairRenderContext,
    ) -> list[str]:
        raise NotImplementedError

    @staticmethod
    def _diagnosis_heading_count(content: str) -> int:
        return sum(1 for heading in DIAGNOSIS_TEMPLATE_HEADINGS if heading in content)

    @staticmethod
    def _source_tips(context: RepairRenderContext, *, limit: int = 3) -> list[str]:
        tips: list[str] = []
        entries = list((context.loaded_context or {}).get("entries") or [])
        for entry in entries:
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            for raw_piece in re.split(r"[\n。；]", content):
                piece = re.sub(r"^\s*(?:#{1,6}\s*|[-*]|\d+[.、）)])\s*", "", raw_piece).strip()
                if not piece:
                    continue
                if RepairKnowledgeFollowupAdapter._looks_like_textual_followup_item(piece):
                    continue
                if piece not in tips:
                    tips.append(piece)
                if len(tips) >= limit:
                    return tips
        return tips

    @staticmethod
    def _structured_evidence_summary(context: RepairRenderContext) -> str:
        evidence = (context.loaded_context or {}).get("structured_evidence") or {}
        if not isinstance(evidence, dict):
            return ""

        parts: list[str] = []
        label_map = {
            "checks": "检查项",
            "thresholds": "阈值/正常表现",
            "actions": "处理动作",
            "recheck": "复验",
        }
        for key in ("checks", "thresholds", "actions", "recheck"):
            values = [str(item).strip() for item in (evidence.get(key) or []) if str(item).strip()]
            if not values:
                continue
            parts.append(f"{label_map[key]}：{' / '.join(values[:3])}")
        return "；".join(parts)

    @staticmethod
    def _count_numbered_steps(content: str) -> int:
        return len(re.findall(r"(?m)^\s*\d+[.、)]\s*", content))

    @staticmethod
    def _has_threshold_or_expected_state(content: str) -> bool:
        return bool(
            re.search(r"\b\d+(?:\.\d+)?\s*(?:v|伏|欧|ohm|%|bar|kpa|a|ma|rpm|转|Ω)\b", content, re.IGNORECASE)
            or any(token in content for token in ("正常", "接近", "应当", "应为", "不应", "偏高", "偏低", "在线", "离线"))
        )

    @staticmethod
    def _has_branching_signal(content: str) -> bool:
        return (
            "如果" in content
            or "若" in content
            or "异常时" in content
            or "正常时" in content
            or "否则" in content
        )

    @staticmethod
    def _has_recheck_signal(content: str) -> bool:
        return any(token in content for token in ("### 复验", "复测", "复验", "再次确认", "路试", "确认报码不再", "确认故障不再出现"))

    def _review_dynamic_contract(self, *, content: str, plan: RepairRenderPlan) -> list[str]:
        reasons: list[str] = []
        if plan.min_steps > 0 and self._count_numbered_steps(content) < plan.min_steps:
            reasons.append("insufficient_step_count")
        if plan.need_thresholds and not self._has_threshold_or_expected_state(content):
            reasons.append("missing_threshold_or_expected_state")
        if plan.need_branching and not self._has_branching_signal(content):
            reasons.append("missing_branching_signal")
        if plan.need_recheck and not self._has_recheck_signal(content):
            reasons.append("missing_recheck_section")
        return reasons


class DtcDiagnosisStrategy(RepairRenderStrategy):
    frame = RepairAnswerFrame.DTC_DIAGNOSIS

    def _frame_contract(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del context
        return (
            "按 `### 当前报码方向`、`### 先查什么`、`### 分步检查`、`### 异常后怎么走`、`### 处理动作`"
            f"{'、`### 复验`' if plan.need_recheck else ''}、`### 易误判点` 组织。"
            "不要展开成通用百科，不要只罗列所有可能原因。"
        )

    def _review_rules(self, *, content: str, plan: RepairRenderPlan, context: RepairRenderContext) -> list[str]:
        del context
        reasons: list[str] = []
        if "### 分步检查" not in content:
            reasons.append("missing_dtc_steps")
        if "### 当前报码方向" not in content:
            reasons.append("missing_dtc_direction")
        if "### 处理动作" not in content:
            reasons.append("missing_dtc_actions")
        reasons.extend(self._review_dynamic_contract(content=content, plan=plan))
        return reasons

    def fallback(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan
        code = context.normalized_query or "当前报码"
        tips = self._source_tips(context)
        first_tip = tips[0] if tips else "先确认报码是当前故障、历史故障还是偶发故障。"
        second_tip = tips[1] if len(tips) > 1 else "再把报码方向和数据流、现象对上，避免只看报码就换件。"
        sections = [
            f"### 当前报码方向\n老哥，{code} 这类问题先别急着换件，先把报码指向的系统方向和现场现象对上。",
            f"### 先查什么\n1. {first_tip}\n2. {second_tip}",
            "### 分步检查\n1. 先确认报码是当前故障、历史故障还是偶发故障，并对应报码出现时的工况。\n2. 再看与该报码最相关的供电、搭铁、线路、信号和执行器状态。\n3. 如果基础条件正常，再继续对照数据流和故障现象往下分支。",
            "### 异常后怎么走\n1. 如果基础供电、搭铁或线路先异常，先把这些前置条件修通，再复测报码。\n2. 如果基础条件正常但数据和现象能对上，再沿报码对应的主线继续深入，不要同时怀疑多个总成。",
            "### 处理动作\n先修最基础的供电、搭铁、线束和插头，再决定是否进入执行器或总成层面。",
        ]
        if context.has_followup:
            sections.append("### 复验\n修复后复现原工况，确认报码不再当前出现，且相关数据和现象恢复一致。")
        sections.append("### 易误判点\n只看到报码就直接换总成，往往会把线路和前置条件问题漏掉。")
        return "\n\n".join(sections)


class SymptomDiagnosisStrategy(RepairRenderStrategy):
    frame = RepairAnswerFrame.SYMPTOM_DIAGNOSIS

    def _frame_contract(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del context
        return (
            "按 `### 当前判断`、`### 检查前准备`、`### 分步检查`、`### 异常后怎么走`、`### 处理动作`"
            f"{'、`### 复验`' if plan.need_recheck else ''}、`### 易误判点` 组织。"
            "必须写成先后顺序和分支判断，不要退回固定的通用诊断模板，也不要先写一大段原因分类。"
        )

    def _review_rules(self, *, content: str, plan: RepairRenderPlan, context: RepairRenderContext) -> list[str]:
        del context
        reasons: list[str] = []
        if "### 分步检查" not in content:
            reasons.append("missing_symptom_steps")
        if "### 当前判断" not in content:
            reasons.append("missing_symptom_judgment")
        if "### 处理动作" not in content:
            reasons.append("missing_symptom_actions")
        reasons.extend(self._review_dynamic_contract(content=content, plan=plan))
        return reasons

    def fallback(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan
        tips = self._source_tips(context)
        while len(tips) < 4:
            defaults = [
                "先确认最稳定复现的主现象，不要把偶发症状和当前主问题混在一起。",
                "先查最基础的供电、搭铁、保险和关键前置条件。",
                "确认第一步正常后，再量关键测点或看关键数据流，不要直接跳到换件。",
                "发现异常后先修前置问题，再复测主现象，避免带着旧故障继续往下拆。",
            ]
            tips.append(defaults[len(tips)])
        sections = [
            f"### 当前判断\n老哥，{context.summary_text or context.query} 这类问题先把主线收紧，先判断基础条件、关键测点还是单个部件先出问题，不要一上来把所有原因铺开。",
            "### 检查前准备\n先确认车辆安全状态、能否稳定复现故障，以及当前报码、供电和已掌握数据是否真实可用。",
            f"### 分步检查\n1. {tips[0]}\n2. {tips[1]}\n3. {tips[2]}",
            f"### 异常后怎么走\n1. 如果第一步就发现基础条件异常，先修基础条件，再回到主故障复测。\n2. 如果基础条件正常但关键测点或数据流异常，沿该主线继续排，不要同时拆多个系统。\n3. {tips[3]}",
            "### 处理动作\n先处理低成本、高概率、能直接改变判断方向的问题点，再决定是否继续拆检执行器或总成。",
        ]
        if context.has_followup:
            sections.append("### 复验\n修复后回到原工况复测，确认主现象消失，相关报码或关键数据恢复正常。")
        sections.append("### 易误判点\n最容易出问题的是答案看起来专业，但落不到先查什么、异常后往哪走。")
        return "\n\n".join(sections)


class SpecAnswerStrategy(RepairRenderStrategy):
    frame = RepairAnswerFrame.SPEC_ANSWER

    def _frame_contract(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan, context
        return (
            "按 `### 直接结论`、`### 适用前提`、`### 现场核对` 组织。"
            "回答要直接，不要转成故障诊断模板。"
        )

    def _review_rules(self, *, content: str, plan: RepairRenderPlan, context: RepairRenderContext) -> list[str]:
        del plan, context
        reasons: list[str] = []
        if self._diagnosis_heading_count(content) >= 2:
            reasons.append("spec_answer_rendered_as_diagnosis")
        if "### 直接结论" not in content:
            reasons.append("missing_spec_conclusion")
        reasons.extend(self._review_dynamic_contract(content=content, plan=plan))
        return reasons

    def fallback(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan
        tips = self._source_tips(context)
        check_tip = tips[0] if tips else "核对对应车型、ECU 或系统版本后再对照标准值。"
        return "\n\n".join(
            [
                f"### 直接结论\n老哥，{context.query} 这类问题优先给结论和适用前提，不按故障排查模板来答。",
                "### 适用前提\n不同车型、系统版本和供电平台可能会有差异，先确认资料是否与当前车辆匹配。",
                f"### 现场核对\n1. {check_tip}\n2. 如果现场测量值和标准值偏差明显，再回头判断是传感器、线路还是供电基准问题。",
            ]
        )


class PrincipleExplanationStrategy(RepairRenderStrategy):
    frame = RepairAnswerFrame.PRINCIPLE_EXPLANATION

    def _frame_contract(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan, context
        return (
            "按 `### 核心原理`、`### 关键输入与输出`、`### 现场怎么用` 组织。"
            "少讲教材式背景，多讲这个原理如何帮助排查。"
        )

    def _review_rules(self, *, content: str, plan: RepairRenderPlan, context: RepairRenderContext) -> list[str]:
        del plan, context
        reasons: list[str] = []
        if self._diagnosis_heading_count(content) >= 2:
            reasons.append("principle_rendered_as_diagnosis")
        if "### 核心原理" not in content:
            reasons.append("missing_principle_section")
        return reasons

    def fallback(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan
        tips = self._source_tips(context)
        system_tip = tips[0] if tips else "先看控制器想让系统做什么，再看执行端有没有真正跟上。"
        return "\n\n".join(
            [
                f"### 核心原理\n老哥，{context.query} 这类问题先抓主链路：控制器根据输入条件计算目标，再通过执行端动作，最后由反馈信号闭环修正。",
                "### 关键输入与输出\n1. 先看哪些输入决定系统是否动作。\n2. 再看执行器或控制输出怎么变化。\n3. 最后看反馈信号是否和目标一致。",
                f"### 现场怎么用\n1. {system_tip}\n2. 如果目标正常而执行没跟上，优先查执行端和线路。\n3. 如果目标本身就不对，再回头查输入条件和控制策略。",
            ]
        )


class LocationIdentificationStrategy(RepairRenderStrategy):
    frame = RepairAnswerFrame.LOCATION_IDENTIFICATION

    def _frame_contract(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan, context
        return (
            "按 `### 先判断是哪一类接口或部件`、`### 通常位置`、`### 现场确认方法`、`### 易混点` 组织。"
            "不要写成诊断排故模板。"
        )

    def _review_rules(self, *, content: str, plan: RepairRenderPlan, context: RepairRenderContext) -> list[str]:
        del plan, context
        reasons: list[str] = []
        if self._diagnosis_heading_count(content) >= 2:
            reasons.append("location_rendered_as_diagnosis")
        if "### 通常位置" not in content and "### 现场确认方法" not in content:
            reasons.append("missing_location_structure")
        return reasons

    def fallback(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan
        known = context.summary_text or context.query
        return "\n\n".join(
            [
                f"### 先判断是哪一类接口或部件\n老哥，{known} 这类问题先别按故障诊断去答，重点是先把接口或部件类别判断准。",
                "### 通常位置\n常见会在驾驶室仪表台下方、座椅旁边控制盒附近、发动机舱线束汇集区，或设备侧面检修盖板内侧这些位置去找。",
                "### 现场确认方法\n1. 先顺着主线束、控制盒和保险盒附近找诊断接口或相关插头。\n2. 看接口针脚数量、壳体形状和线色分布，避免把相邻保养口或附件接口认错。\n3. 找到后再核对线束走向和对应系统标签。",
                "### 易混点\n最容易把外接附件口、保养口或相邻传感器插头误当成目标接口，现场一定要结合线束去向再确认。",
            ]
        )


class OperationGuideStrategy(RepairRenderStrategy):
    frame = RepairAnswerFrame.OPERATION_GUIDE

    def _frame_contract(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan, context
        return (
            "按 `### 适用场景`、`### 操作步骤`、`### 成功判据`、`### 注意事项` 组织。"
            "不要切回故障诊断模板。"
        )

    def _review_rules(self, *, content: str, plan: RepairRenderPlan, context: RepairRenderContext) -> list[str]:
        del plan, context
        reasons: list[str] = []
        if self._diagnosis_heading_count(content) >= 2:
            reasons.append("operation_rendered_as_diagnosis")
        if "### 操作步骤" not in content:
            reasons.append("missing_operation_steps")
        reasons.extend(self._review_dynamic_contract(content=content, plan=plan))
        return reasons

    def fallback(self, *, plan: RepairRenderPlan, context: RepairRenderContext) -> str:
        del plan
        return "\n\n".join(
            [
                f"### 适用场景\n老哥，{context.query} 这类问题重点是把操作前提和步骤讲清楚，不是先讲故障原因。",
                "### 操作步骤\n1. 先确认设备、软件或车辆状态满足操作前提。\n2. 按顺序执行关键步骤，每一步都看反馈是否正常。\n3. 如果中间某一步反馈不对，先停在该步回头查前置条件，不要硬往下做。",
                "### 成功判据\n操作完成后要看目标状态是否真正建立，而不是只看界面是否显示执行完成。",
                "### 注意事项\n涉及写入、刷写、搭电或带电插拔的动作，先确认电压稳定和风险前提。",
            ]
        )


_STRATEGIES: dict[RepairAnswerFrame, RepairRenderStrategy] = {
    RepairAnswerFrame.DTC_DIAGNOSIS: DtcDiagnosisStrategy(),
    RepairAnswerFrame.SYMPTOM_DIAGNOSIS: SymptomDiagnosisStrategy(),
    RepairAnswerFrame.SPEC_ANSWER: SpecAnswerStrategy(),
    RepairAnswerFrame.PRINCIPLE_EXPLANATION: PrincipleExplanationStrategy(),
    RepairAnswerFrame.LOCATION_IDENTIFICATION: LocationIdentificationStrategy(),
    RepairAnswerFrame.OPERATION_GUIDE: OperationGuideStrategy(),
}


def get_repair_render_strategy(frame: RepairAnswerFrame) -> RepairRenderStrategy:
    return _STRATEGIES[frame]


def review_repair_rendered_answer(
    *,
    content: str,
    plan: RepairRenderPlan,
    context: RepairRenderContext,
) -> RepairRenderedReview:
    return get_repair_render_strategy(plan.frame).review(content=content, plan=plan, context=context)


def build_repair_render_fallback_content(
    *,
    plan: RepairRenderPlan,
    context: RepairRenderContext,
) -> str:
    return get_repair_render_strategy(plan.frame).fallback(plan=plan, context=context)


def _default_frame_for_context(context: RepairRenderContext) -> RepairAnswerFrame:
    if context.has_fault_code:
        return RepairAnswerFrame.DTC_DIAGNOSIS
    if context.has_principle_signal:
        return RepairAnswerFrame.PRINCIPLE_EXPLANATION
    if context.has_location_signal:
        return RepairAnswerFrame.LOCATION_IDENTIFICATION
    if context.has_operation_signal:
        return RepairAnswerFrame.OPERATION_GUIDE
    if context.has_spec_signal and not context.has_symptom_signal:
        return RepairAnswerFrame.SPEC_ANSWER
    return RepairAnswerFrame.SYMPTOM_DIAGNOSIS


def _has_strong_frame_signal(frame: RepairAnswerFrame, context: RepairRenderContext) -> bool:
    if frame == RepairAnswerFrame.DTC_DIAGNOSIS:
        return context.has_fault_code
    if frame == RepairAnswerFrame.PRINCIPLE_EXPLANATION:
        return context.has_principle_signal
    if frame == RepairAnswerFrame.LOCATION_IDENTIFICATION:
        return context.has_location_signal
    if frame == RepairAnswerFrame.OPERATION_GUIDE:
        return context.has_operation_signal
    if frame == RepairAnswerFrame.SPEC_ANSWER:
        return context.has_spec_signal and not context.has_symptom_signal
    return context.has_symptom_signal


def _combined_context_text(context: RepairRenderContext) -> str:
    return "\n".join(
        part
        for part in [context.normalized_query, context.summary_text, context.flattened_fields]
        if part
    ).lower()


def _default_depth_for_context(
    *,
    frame: RepairAnswerFrame,
    context: RepairRenderContext,
) -> RepairAnswerDepth:
    combined = _combined_context_text(context)
    has_structured_sources = bool((context.loaded_context or {}).get("structured_evidence"))
    if frame == RepairAnswerFrame.SPEC_ANSWER:
        return RepairAnswerDepth.DIRECT
    if frame in {RepairAnswerFrame.LOCATION_IDENTIFICATION, RepairAnswerFrame.PRINCIPLE_EXPLANATION}:
        return RepairAnswerDepth.STANDARD
    if frame == RepairAnswerFrame.OPERATION_GUIDE:
        return RepairAnswerDepth.STANDARD if context.has_followup else RepairAnswerDepth.DIRECT
    if (
        context.has_followup
        or has_structured_sources
        or any(hint in combined for hint in RepairKnowledgeFollowupAdapter.COMMUNICATION_HINTS)
        or any(hint in combined for hint in RepairKnowledgeFollowupAdapter.ELECTRICAL_REPAIR_HINTS)
    ):
        return RepairAnswerDepth.PLAYBOOK
    return RepairAnswerDepth.STANDARD


def _build_required_elements(
    *,
    frame: RepairAnswerFrame,
    depth: RepairAnswerDepth,
) -> tuple[list[str], list[str]]:
    required_map = {
        RepairAnswerFrame.DTC_DIAGNOSIS: ["报码方向", "检查顺序", "异常分支", "处理动作"],
        RepairAnswerFrame.SYMPTOM_DIAGNOSIS: ["当前判断", "分步检查", "异常分支", "处理动作"],
        RepairAnswerFrame.SPEC_ANSWER: ["直接结论", "适用前提", "现场核对"],
        RepairAnswerFrame.PRINCIPLE_EXPLANATION: ["核心原理", "关键输入输出", "现场怎么用"],
        RepairAnswerFrame.LOCATION_IDENTIFICATION: ["通常位置", "现场确认方法"],
        RepairAnswerFrame.OPERATION_GUIDE: ["操作步骤", "成功判据", "注意事项"],
    }
    optional_map = {
        RepairAnswerFrame.DTC_DIAGNOSIS: ["阈值或正常表现", "复验", "易误判点"],
        RepairAnswerFrame.SYMPTOM_DIAGNOSIS: ["检查前准备", "阈值或正常表现", "复验", "易误判点"],
        RepairAnswerFrame.SPEC_ANSWER: ["误差判断", "延伸说明"],
        RepairAnswerFrame.PRINCIPLE_EXPLANATION: ["常见误区", "现场判断捷径"],
        RepairAnswerFrame.LOCATION_IDENTIFICATION: ["易混点", "线束走向提示"],
        RepairAnswerFrame.OPERATION_GUIDE: ["失败分支", "风险提示"],
    }
    required = list(required_map[frame])
    optional = list(optional_map[frame])
    if depth == RepairAnswerDepth.PLAYBOOK and "复验" not in required and frame in {
        RepairAnswerFrame.DTC_DIAGNOSIS,
        RepairAnswerFrame.SYMPTOM_DIAGNOSIS,
    }:
        required.append("复验")
    return required[:8], optional[:8]


def _default_min_steps(*, frame: RepairAnswerFrame, depth: RepairAnswerDepth) -> int:
    if frame == RepairAnswerFrame.SPEC_ANSWER:
        return 1
    if frame == RepairAnswerFrame.OPERATION_GUIDE:
        return 3 if depth != RepairAnswerDepth.DIRECT else 2
    if frame in {RepairAnswerFrame.DTC_DIAGNOSIS, RepairAnswerFrame.SYMPTOM_DIAGNOSIS}:
        return 4 if depth == RepairAnswerDepth.PLAYBOOK else 3
    if frame == RepairAnswerFrame.LOCATION_IDENTIFICATION:
        return 2
    return 1


def _default_need_thresholds(*, frame: RepairAnswerFrame, context: RepairRenderContext) -> bool:
    if frame == RepairAnswerFrame.SPEC_ANSWER:
        return True
    if frame not in {RepairAnswerFrame.DTC_DIAGNOSIS, RepairAnswerFrame.SYMPTOM_DIAGNOSIS}:
        return False
    combined = _combined_context_text(context)
    return any(
        hint in combined
        for hint in (
            "电压",
            "电阻",
            "阻值",
            "压降",
            "5v",
            "5伏",
            "can",
            "j1939",
            "轨压",
            "报码",
            "报码偶发",
        )
    )


def _default_need_branching(*, frame: RepairAnswerFrame) -> bool:
    return frame in {
        RepairAnswerFrame.DTC_DIAGNOSIS,
        RepairAnswerFrame.SYMPTOM_DIAGNOSIS,
        RepairAnswerFrame.OPERATION_GUIDE,
    }


def _default_need_recheck(
    *,
    frame: RepairAnswerFrame,
    depth: RepairAnswerDepth,
) -> bool:
    if frame in {RepairAnswerFrame.DTC_DIAGNOSIS, RepairAnswerFrame.SYMPTOM_DIAGNOSIS}:
        return depth != RepairAnswerDepth.DIRECT
    if frame == RepairAnswerFrame.OPERATION_GUIDE:
        return True
    return False


def _build_focus_points(
    *,
    frame: RepairAnswerFrame,
    context: RepairRenderContext,
) -> list[str]:
    focus: list[str] = []
    if context.summary_text:
        focus.append("优先吸收用户刚补充的现象、工况、报码或系统信息")
    if context.source_titles:
        focus.append("优先结合已加载维修资料，不要丢掉主参考标题")
    if frame == RepairAnswerFrame.DTC_DIAGNOSIS:
        focus.append("先解释报码方向，再给检查顺序、异常分支和处理动作")
    elif frame == RepairAnswerFrame.SYMPTOM_DIAGNOSIS:
        focus.append("先分主线，再写现场分步检查、异常后怎么走和处理动作")
    elif frame == RepairAnswerFrame.SPEC_ANSWER:
        focus.append("先给参数结论，再说明适用前提和现场核对")
    elif frame == RepairAnswerFrame.PRINCIPLE_EXPLANATION:
        focus.append("把原理讲到能指导现场判断，而不是讲成教材")
    elif frame == RepairAnswerFrame.LOCATION_IDENTIFICATION:
        focus.append("重点回答接口或部件在哪、怎么确认、容易和什么混淆")
    elif frame == RepairAnswerFrame.OPERATION_GUIDE:
        focus.append("按步骤写，给出成功判据、异常分支和注意事项")
    return focus[:4]
