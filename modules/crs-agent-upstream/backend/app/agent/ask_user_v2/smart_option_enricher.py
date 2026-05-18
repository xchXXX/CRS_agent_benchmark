"""LLM-assisted option enrichment for text-first ask-user prompts."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.agent.ask_user_v2.schema import AskUserFormOption
from app.agent.model_ids import normalize_configured_model
from app.agent.models.ask_user import AskUserInputType, AskUserQuestion
from app.core.config import Settings, settings as app_settings
from app.legacy.services.config_service import config_service


logger = logging.getLogger(__name__)


MAX_REPAIR_FOLLOWUP_FIELDS = 10
MAX_REPAIR_FOLLOWUP_OPTIONS = 5


SMART_ASK_USER_OPTION_INSTRUCTIONS = """
你在为维修专家系统生成 Ask User 候选项。

目标：
- 用户不应只看到纯填空题；
- 任何 ask user 追问都应优先给 3 到 5 个可点击候选项；
- 如果无法精确到具体型号，可以退到品牌级、车系级、车型级、吨位级或系统级，但仍要便于用户直接点选。

要求：
1. 候选项必须贴合用户原问题和当前追问语境。
2. 候选项要短、自然、适合按钮展示。
3. 禁止输出“其他”“不确定”“待补充”“请手动输入”这类兜底项。
4. 候选项之间不要重复，不要只是同一句话的改写。
5. 如果用户已经明确了品牌或设备类型，要保留这个语境。
6. 对工程机械场景，如果只知道品牌而不知道型号，可优先给常见吨位/机型层级候选。
7. 如果字段是品牌/车系/车型/发动机/系统信息，优先生成这类信息的最可能选项，而不是退回空列表。
8. 候选项必须是扁平的一组答案，不能是步骤、流程、操作动作、上传动作或组合指令。
9. 禁止输出“上传图片”“上传文件”“截图”“拍照”“补充视频”“导出数据流”这类过程项。
10. 禁止输出“已确认/已核对/已记录/已掌握”这类没有具体故障内容的低信息选项。
11. 只返回结构化结果，不要解释。
""".strip()


REPAIR_FOLLOWUP_OPTION_INSTRUCTIONS = """
你在为维修专家系统生成 Ask User 卡片中的字段候选项。

目标：
- 不要把用户逼成纯填空；
- 要为当前这个字段生成 3 到 5 个最可能、最适合点选的候选项；
- 候选项必须贴合用户当前故障描述和已知资料上下文。

字段含义：
- `fault_phenomenon`：输出可观察到的故障现象，不要输出问题句。
- `working_condition`：输出故障最容易出现的工况、环境或触发条件。
- `fault_codes`：输出最可能的报码/报码方向；如果无法确定具体报码，也可以输出少量报码状态。
- `ecu_or_system`：输出最可能涉事的车型、发动机、ECU、系统或部件范围。
- `data_evidence`：输出技师手上可能已经读到的异常结果、关键数值范围或正常/异常分型。
- `repair_history`：输出近期更换、检修或处理过的项目。

要求：
1. 每个候选项都要短、自然、适合按钮展示。
2. 禁止输出“其他”“不确定”“待补充”“请手动输入”“请补充信息”之类兜底项。
3. 禁止把整句反问、整段解释或完整问句当成候选项。
4. 候选项之间不要重复，也不要只是同一句话的轻微改写。
5. 优先输出能帮助诊断分型的信息，而不是泛化词。
6. 候选项必须是用户可直接点选的“答案”，不能是“下一步去做什么”。
7. 禁止输出上传、截图、拍照、录视频、导出文件、读取数据流、继续检查、先测再说这类过程项或动作项。
8. 如果字段是 `data_evidence`，也只能输出具体观测结果，例如“轨压跟不上目标值”“主干电阻不在 60 欧附近”“CAN 电压被拉低”；不要输出“已测某项/已确认某项/已查看某项”。
9. 候选项必须扁平互斥，避免“报码”和“当前有报码”“报码存在”这种意思重复的选项同时出现。
10. 禁止输出“已确认/已核对/已记录/已掌握”这类没有具体异常、数值或报码内容的低信息选项。
11. 像医生问诊一样生成选项：优先围绕外在表现、报码内容、触发工况、关键异常观测和近期维修影响来问。
12. 只返回结构化结果，不要解释。
""".strip()


class SmartAskUserCandidate(BaseModel):
    label: str
    description: str | None = None


class SmartAskUserFieldSuggestion(BaseModel):
    title: str | None = None
    field_label: str | None = None
    input_hint: str | None = None
    options: list[SmartAskUserCandidate] = Field(default_factory=list)


class RepairFollowupOptionSuggestion(BaseModel):
    options: list[SmartAskUserCandidate] = Field(default_factory=list)


class RepairFollowupFieldPlan(BaseModel):
    key: str
    label: str | None = None
    selection_mode: str | None = None
    placeholder: str | None = None
    hint: str | None = None
    options: list[SmartAskUserCandidate] = Field(default_factory=list)


class RepairFollowupPlanSuggestion(BaseModel):
    ask_reason: str | None = None
    fields: list[RepairFollowupFieldPlan] = Field(default_factory=list)


REPAIR_FOLLOWUP_PLAN_INSTRUCTIONS = """
你在为维修专家系统规划一张追问信息卡。

目标：
- 不是把可能缺的信息全问一遍，而是只问最能改变诊断路径的问题，最多 10 个，优先少问；
- 每个问题都要让技师可以快速点选，不要把他逼成纯填空；
- 选项必须是最可能出现、且能明显帮助下一步分型或分支判断的候选项。
- 追问方式要像医生问诊：从外在表现、报码内容、触发工况、关键异常观测、近期维修影响出发，而不是问“是否已确认/是否已记录”。

你要优先追求“信息增益”：
1. 先问能最大幅度缩小故障范围的问题；
2. 避免问已经在用户原话或资料里明确给出的信息；
3. 避免两个字段本质上在问同一件事；
4. 如果一个问题只会得到泛化答案，就不要问它。

字段限制：
- `key` 只允许使用：`fault_phenomenon`、`working_condition`、`fault_codes`、`ecu_or_system`、`data_evidence`、`repair_history`
- `selection_mode` 只允许：`single`、`multi`、`mixed`
- 每个字段尽量提供 3 到 5 个候选项；如果确实无法可靠收敛，可以少于 3 个，但不要空想泛化项
- 候选项必须是用户可直接选择的答案，不是信息类别、说明句、追问句、模板句
- 禁止输出“其他”“不确定”“待补充”“请手动输入”这类兜底词
- 禁止输出“上传图片/截图/文件/视频/数据流”“拍照上传”“导出日志”“继续检查”这类过程项
- 即使是 `data_evidence`，也只能输出具体异常结果、正常/异常分型或关键数值范围，不要输出文件类型、资料类别、上传入口、“已测/已查看”或“已确认/已核对”这类空信息
- 避免意思相同或高度相近的候选同时出现，例如“当前有报码/已有报码/报码存在”

字段偏好：
- `fault_phenomenon`：要问可观察现象，优先能帮助分型的现象差异
- `working_condition`：要问触发条件、冷热车、负载、时机等
- `fault_codes`：优先给最可能的报码方向或报码状态
- `ecu_or_system`：优先给最可能涉事系统/车型/ECU，不要泛泛写“请补充车型”
- `data_evidence`：只能问“关键结果呈现什么状态”，不是问做过什么检查，也不是让用户上传资料类别
- `repair_history`：只问近期更换/维修/拆装过的关键项目

输出要求：
- `ask_reason` 用一句短中文说明“为什么现在要补这些信息”
- `fields` 按重要性从高到低排序
- 只返回结构化结果，不要解释
""".strip()


class SmartAskUserOptionEnricher:
    """Predict click-friendly options for text-only ask-user prompts."""

    MODEL_TRIGGER_PATTERN = re.compile(
        r"(型号|机型|吨位|车系|平台|发动机型号|机号|机种|ecu型号|系统型号|控制器型号)",
        re.IGNORECASE,
    )
    EXCAVATOR_HINT_PATTERN = re.compile(r"(挖机|挖掘机|履带挖|轮挖)", re.IGNORECASE)
    TRUCK_HINT_PATTERN = re.compile(r"(卡车|货车|牵引车|载货车|轻卡|中卡|重卡)", re.IGNORECASE)
    ECU_HINT_PATTERN = re.compile(r"(ecu|电脑|控制器|后处理|发动机系统|系统型号)", re.IGNORECASE)
    LOCATION_HINT_PATTERN = re.compile(r"(检测口|诊断口|接口|插口|位置|在哪|在哪里)", re.IGNORECASE)
    INVALID_OPTION_PATTERN = re.compile(r"(其他|不确定|待补充|手动输入|请输入|请补充)", re.IGNORECASE)
    VEHICLE_INFO_PATTERN = re.compile(r"(品牌|车系|车型|整车型号|发动机型号|系统信息|车辆信息)", re.IGNORECASE)
    PROCESS_OPTION_PATTERN = re.compile(
        r"(上传|截图|拍照|照片|图片|文件|视频|录屏|导出|csv|excel|日志|附件|资料|继续检查|进一步检查|先测|去测|读取数据流|查看数据流)",
        re.IGNORECASE,
    )
    LOW_INFORMATION_OPTION_PATTERN = re.compile(
        r"(?:已|已经)?(?:确认|核对|记录|复现|掌握)|(?:已|已经)(?:测|查看|检查)",
        re.IGNORECASE,
    )
    DIAGNOSTIC_VALUE_PATTERN = re.compile(
        r"([PBUC][0-9A-Z]{4}|异常|偏低|偏高|过低|过高|不在|不到|超过|低于|高于|不上去|跟不上|不工作|离线|中断|丢失|短路|开路|断路|虚接|泄漏|结晶|限扭|激活|无力|熄火|抖动|冒烟|[0-9]+(?:\\.?[0-9]+)?\\s*(?:v|伏|欧|mpa|bar|℃|度))",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        config_service_override: Any | None = None,
        model_override: Any | None = None,
    ) -> None:
        self._settings = settings or app_settings
        self._config_service = config_service_override or config_service
        self._model_override = model_override
        self._agent = None
        self._agent_signature: tuple[Any, int, float, float] | None = None
        self._repair_followup_agent = None
        self._repair_followup_agent_signature: tuple[Any, int, float, float] | None = None
        self._repair_followup_plan_agent = None
        self._repair_followup_plan_agent_signature: tuple[Any, int, float, float] | None = None

    def maybe_build_field_suggestion(
        self,
        *,
        ask_user: AskUserQuestion,
    ) -> SmartAskUserFieldSuggestion | None:
        if not self._should_enrich_ask_user(ask_user):
            return None

        context = dict(ask_user.context or {})
        prompt_text = self._build_prompt_text(
            query=str(context.get("query") or context.get("repair_knowledge_query") or "").strip(),
            question=ask_user.question,
            input_hint=ask_user.input_hint,
            context=context,
        )
        defaults = self._build_default_field_suggestion(prompt_text=prompt_text, input_hint=ask_user.input_hint)

        prediction = self._predict_with_model(prompt_text=prompt_text)
        normalized = self._normalize_prediction(prediction, defaults=defaults)
        if normalized is not None:
            return normalized

        return self._build_fallback_suggestion(prompt_text=prompt_text, defaults=defaults)

    async def maybe_build_field_suggestion_async(
        self,
        *,
        ask_user: AskUserQuestion,
    ) -> SmartAskUserFieldSuggestion | None:
        if not self._should_enrich_ask_user(ask_user):
            return None

        context = dict(ask_user.context or {})
        prompt_text = self._build_prompt_text(
            query=str(context.get("query") or context.get("repair_knowledge_query") or "").strip(),
            question=ask_user.question,
            input_hint=ask_user.input_hint,
            context=context,
        )
        defaults = self._build_default_field_suggestion(prompt_text=prompt_text, input_hint=ask_user.input_hint)

        prediction = await self._predict_with_model_async(prompt_text=prompt_text)
        normalized = self._normalize_prediction(prediction, defaults=defaults)
        if normalized is not None:
            return normalized

        return self._build_fallback_suggestion(prompt_text=prompt_text, defaults=defaults)

    def suggest_model_option_labels(
        self,
        *,
        query: str,
        input_hint: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[str]:
        prompt_text = self._build_prompt_text(
            query=query,
            question=query,
            input_hint=input_hint,
            context=context or {},
        )
        if not self._should_enrich_text(prompt_text):
            return []

        defaults = self._build_default_field_suggestion(prompt_text=prompt_text, input_hint=input_hint)
        prediction = self._predict_with_model(prompt_text=prompt_text)
        normalized = self._normalize_prediction(prediction, defaults=defaults)
        if normalized is None:
            normalized = self._build_fallback_suggestion(prompt_text=prompt_text, defaults=defaults)
        if normalized is None:
            return []
        return [option.label for option in normalized.options]

    async def suggest_model_option_labels_async(
        self,
        *,
        query: str,
        input_hint: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[str]:
        prompt_text = self._build_prompt_text(
            query=query,
            question=query,
            input_hint=input_hint,
            context=context or {},
        )
        if not self._should_enrich_text(prompt_text):
            return []

        defaults = self._build_default_field_suggestion(prompt_text=prompt_text, input_hint=input_hint)
        prediction = await self._predict_with_model_async(prompt_text=prompt_text)
        normalized = self._normalize_prediction(prediction, defaults=defaults)
        if normalized is None:
            normalized = self._build_fallback_suggestion(prompt_text=prompt_text, defaults=defaults)
        if normalized is None:
            return []
        return [option.label for option in normalized.options]

    def suggest_repair_followup_option_labels(
        self,
        *,
        query: str,
        field_key: str,
        field_label: str,
        input_hint: str | None = None,
        loaded_context: dict[str, Any] | None = None,
    ) -> list[str]:
        model = self._resolve_model()
        if not model or model == "test":
            return []
        if self._has_running_loop():
            logger.debug("repair followup option enrichment skipped in async runtime; fallback to deterministic presets")
            return []

        prompt_text = self._build_repair_followup_prompt_text(
            query=query,
            field_key=field_key,
            field_label=field_label,
            input_hint=input_hint,
            loaded_context=loaded_context,
        )
        try:
            agent = self._get_repair_followup_agent(model=model, max_tokens=600, temperature=0.2, timeout=12.0)
            result = agent.run_sync(user_prompt=prompt_text)
        except Exception as exc:
            logger.warning("repair followup option enrichment failed, fallback to deterministic presets. reason=%s", exc)
            return []

        return self._normalize_repair_followup_prediction(result.output)

    async def suggest_repair_followup_option_labels_async(
        self,
        *,
        query: str,
        field_key: str,
        field_label: str,
        input_hint: str | None = None,
        loaded_context: dict[str, Any] | None = None,
    ) -> list[str]:
        model = self._resolve_model()
        if not model or model == "test":
            return []

        prompt_text = self._build_repair_followup_prompt_text(
            query=query,
            field_key=field_key,
            field_label=field_label,
            input_hint=input_hint,
            loaded_context=loaded_context,
        )
        try:
            agent = self._get_repair_followup_agent(model=model, max_tokens=600, temperature=0.2, timeout=12.0)
            result = await agent.run(user_prompt=prompt_text)
        except Exception as exc:
            logger.warning("repair followup async option enrichment failed, fallback to deterministic presets. reason=%s", exc)
            return []

        return self._normalize_repair_followup_prediction(result.output)

    def suggest_repair_followup_plan(
        self,
        *,
        query: str,
        answer_text: str | None = None,
        loaded_context: dict[str, Any] | None = None,
    ) -> RepairFollowupPlanSuggestion | None:
        model = self._resolve_model()
        if not model or model == "test":
            return None
        if self._has_running_loop():
            logger.debug("repair followup planner skipped in async runtime")
            return None

        prompt_text = self._build_repair_followup_plan_prompt_text(
            query=query,
            answer_text=answer_text,
            loaded_context=loaded_context,
        )
        try:
            agent = self._get_repair_followup_plan_agent(model=model, max_tokens=1800, temperature=0.2, timeout=18.0)
            result = agent.run_sync(user_prompt=prompt_text)
        except Exception as exc:
            logger.warning("repair followup planner failed, fallback to deterministic planning. reason=%s", exc)
            return None

        return self._normalize_repair_followup_plan_prediction(result.output)

    async def suggest_repair_followup_plan_async(
        self,
        *,
        query: str,
        answer_text: str | None = None,
        loaded_context: dict[str, Any] | None = None,
    ) -> RepairFollowupPlanSuggestion | None:
        model = self._resolve_model()
        if not model or model == "test":
            return None

        prompt_text = self._build_repair_followup_plan_prompt_text(
            query=query,
            answer_text=answer_text,
            loaded_context=loaded_context,
        )
        try:
            agent = self._get_repair_followup_plan_agent(model=model, max_tokens=1800, temperature=0.2, timeout=18.0)
            result = await agent.run(user_prompt=prompt_text)
        except Exception as exc:
            logger.warning("repair followup async planner failed, fallback to deterministic planning. reason=%s", exc)
            return None

        return self._normalize_repair_followup_plan_prediction(result.output)

    def _should_enrich_ask_user(self, ask_user: AskUserQuestion) -> bool:
        if ask_user.input_type not in {AskUserInputType.TEXT, AskUserInputType.SINGLE_SELECT}:
            return False
        if ask_user.options:
            return False

        context = dict(ask_user.context or {})
        scene = str(context.get("scene") or "").strip().lower()
        card_type = str(context.get("card_type") or "").strip().lower()
        if scene in {"doc_search", "repair_knowledge_followup"}:
            return False
        if card_type in {"repair_followup"}:
            return False

        prompt_text = self._build_prompt_text(
            query=str(context.get("query") or context.get("repair_knowledge_query") or "").strip(),
            question=ask_user.question,
            input_hint=ask_user.input_hint,
            context=context,
        )
        return self._should_enrich_text(prompt_text)

    def _should_enrich_text(self, prompt_text: str) -> bool:
        normalized = prompt_text.strip()
        if not normalized:
            return False
        if self._looks_like_answered_prompt(normalized):
            return False
        return True

    @staticmethod
    def _looks_like_answered_prompt(prompt_text: str) -> bool:
        normalized = prompt_text.strip()
        if not normalized:
            return True
        if "当前追问：" in normalized:
            question = normalized.split("当前追问：", 1)[1].splitlines()[0].strip()
            if not question:
                return True
        return False

    @staticmethod
    def _build_prompt_text(
        *,
        query: str,
        question: str,
        input_hint: str | None,
        context: dict[str, Any],
    ) -> str:
        form = context.get("form") if isinstance(context.get("form"), dict) else None
        field_label = ""
        field_key = ""
        if form and isinstance(form.get("sections"), list) and form.get("sections"):
            first_section = form.get("sections")[0]
            if isinstance(first_section, dict) and isinstance(first_section.get("fields"), list) and first_section.get("fields"):
                first_field = first_section.get("fields")[0]
                if isinstance(first_field, dict):
                    field_label = str(first_field.get("label") or "").strip()
                    field_key = str(first_field.get("key") or "").strip()
        parts = [
            f"用户原问题：{query}" if query else "",
            f"当前追问：{question}" if question else "",
            f"当前字段：{field_label}" if field_label else "",
            f"当前字段 key：{field_key}" if field_key else "",
            f"输入提示：{input_hint}" if input_hint else "",
            f"场景：{context.get('scene')}" if context.get("scene") else "",
        ]
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _build_repair_followup_prompt_text(
        *,
        query: str,
        field_key: str,
        field_label: str,
        input_hint: str | None,
        loaded_context: dict[str, Any] | None,
    ) -> str:
        context_parts: list[str] = []
        if isinstance(loaded_context, dict):
            source_refs = loaded_context.get("source_refs") or []
            if source_refs:
                context_parts.append(
                    "来源标题：" + "；".join(str(item.get("title") or "").strip() for item in source_refs[:3] if str(item.get("title") or "").strip())
                )
            llm_context = str(loaded_context.get("llm_context") or "").strip()
            if llm_context:
                compact = re.sub(r"\s+", " ", llm_context)
                context_parts.append(f"资料摘要：{compact[:600]}")

        parts = [
            f"用户原问题：{query.strip()}",
            f"当前字段 key：{field_key}",
            f"当前字段标题：{field_label.strip()}",
            f"当前字段提示：{str(input_hint or '').strip() or '无'}",
            *context_parts,
        ]
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _build_repair_followup_plan_prompt_text(
        *,
        query: str,
        answer_text: str | None,
        loaded_context: dict[str, Any] | None,
    ) -> str:
        context_parts: list[str] = []
        if isinstance(loaded_context, dict):
            source_refs = loaded_context.get("source_refs") or []
            if source_refs:
                titles = [str(item.get("title") or "").strip() for item in source_refs[:3] if str(item.get("title") or "").strip()]
                if titles:
                    context_parts.append("来源标题：" + "；".join(titles))
            llm_context = str(loaded_context.get("llm_context") or "").strip()
            if llm_context:
                compact = re.sub(r"\s+", " ", llm_context)
                context_parts.append(f"资料摘要：{compact[:900]}")

        if answer_text:
            compact_answer = re.sub(r"\s+", " ", str(answer_text).strip())
            if compact_answer:
                context_parts.append(f"当前回答草稿或资料归纳：{compact_answer[:900]}")

        parts = [
            f"用户原问题：{query.strip()}",
            *context_parts,
        ]
        return "\n".join(part for part in parts if part).strip()

    def _build_default_field_suggestion(
        self,
        *,
        prompt_text: str,
        input_hint: str | None,
    ) -> SmartAskUserFieldSuggestion:
        normalized = prompt_text.lower()
        if self.VEHICLE_INFO_PATTERN.search(normalized):
            return SmartAskUserFieldSuggestion(
                title="车辆信息确认",
                field_label="品牌/车系/发动机信息",
                input_hint=input_hint or "优先点选最接近的品牌、车系或发动机；没有合适项时再手动补充",
            )
        if self.EXCAVATOR_HINT_PATTERN.search(normalized):
            return SmartAskUserFieldSuggestion(
                title="设备型号确认",
                field_label="挖机型号或吨位",
                input_hint=input_hint or "没有合适项时，可直接补充具体型号或吨位",
            )
        if self.ECU_HINT_PATTERN.search(normalized):
            return SmartAskUserFieldSuggestion(
                title="系统型号确认",
                field_label="ECU/系统型号",
                input_hint=input_hint or "没有合适项时，可直接补充 ECU 或系统型号",
            )
        if self.TRUCK_HINT_PATTERN.search(normalized):
            return SmartAskUserFieldSuggestion(
                title="车型信息确认",
                field_label="车型或发动机型号",
                input_hint=input_hint or "没有合适项时，可直接补充具体车型或发动机型号",
            )
        return SmartAskUserFieldSuggestion(
            title="关键型号确认",
            field_label="型号或系统信息",
            input_hint=input_hint or "没有合适项时，可直接补充更具体的型号信息",
        )

    def _predict_with_model(self, *, prompt_text: str) -> SmartAskUserFieldSuggestion | None:
        model = self._resolve_model()
        if not model or model == "test":
            return None
        if self._has_running_loop():
            logger.debug("smart ask-user option enrichment skipped in async runtime; fallback to heuristic presets")
            return None

        try:
            agent = self._get_agent(model=model, max_tokens=600, temperature=0.15, timeout=12.0)
            result = agent.run_sync(user_prompt=prompt_text)
            return result.output
        except Exception as exc:
            logger.warning("smart ask-user option enrichment failed, fallback to heuristic. reason=%s", exc)
            return None

    async def _predict_with_model_async(self, *, prompt_text: str) -> SmartAskUserFieldSuggestion | None:
        model = self._resolve_model()
        if not model or model == "test":
            return None

        try:
            agent = self._get_agent(model=model, max_tokens=600, temperature=0.15, timeout=12.0)
            result = await agent.run(user_prompt=prompt_text)
            return result.output
        except Exception as exc:
            logger.warning("smart ask-user async option enrichment failed, fallback to heuristic. reason=%s", exc)
            return None

    def _normalize_prediction(
        self,
        prediction: SmartAskUserFieldSuggestion | None,
        *,
        defaults: SmartAskUserFieldSuggestion,
    ) -> SmartAskUserFieldSuggestion | None:
        if prediction is None:
            return None

        options: list[SmartAskUserCandidate] = []
        seen: set[str] = set()
        for item in prediction.options:
            label = str(item.label or "").strip()
            if not label or self.INVALID_OPTION_PATTERN.search(label):
                continue
            normalized = label.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            options.append(
                SmartAskUserCandidate(
                    label=label,
                    description=str(item.description or "").strip() or None,
                )
            )

        if not options:
            return None

        return SmartAskUserFieldSuggestion(
            title=str(prediction.title or defaults.title or "").strip() or defaults.title,
            field_label=str(prediction.field_label or defaults.field_label or "").strip() or defaults.field_label,
            input_hint=str(prediction.input_hint or defaults.input_hint or "").strip() or defaults.input_hint,
            options=options[:5],
        )

    def _build_fallback_suggestion(
        self,
        *,
        prompt_text: str,
        defaults: SmartAskUserFieldSuggestion,
    ) -> SmartAskUserFieldSuggestion | None:
        normalized = prompt_text.lower()
        labels: list[str] = []

        if self.VEHICLE_INFO_PATTERN.search(normalized):
            labels = [
                "东风",
                "解放",
                "重汽",
                "陕汽",
                "福田",
            ]
        elif self.EXCAVATOR_HINT_PATTERN.search(normalized):
            labels = [
                "6 吨级",
                "15 吨级",
                "20 到 22 吨级",
                "30 吨级以上",
            ]
        elif self.TRUCK_HINT_PATTERN.search(normalized):
            labels = [
                "轻卡",
                "中卡",
                "重卡",
                "牵引车",
            ]
        elif self.ECU_HINT_PATTERN.search(normalized) and self.LOCATION_HINT_PATTERN.search(normalized):
            labels = [
                "发动机 ECU",
                "后处理控制器",
                "仪表/车身控制器",
                "变速箱控制器",
            ]

        if not labels:
            return None

        return SmartAskUserFieldSuggestion(
            title=defaults.title,
            field_label=defaults.field_label,
            input_hint=defaults.input_hint,
            options=[SmartAskUserCandidate(label=label) for label in labels],
        )

    def _resolve_model(self) -> Any:
        raw_model = self._model_override
        if raw_model is None:
            raw_model = self._config_service.get("agent_model", self._settings.agent_model)
        return self._normalize_model(raw_model)

    @staticmethod
    def _normalize_model(model: Any) -> Any:
        return normalize_configured_model(model)

    @staticmethod
    def _has_running_loop() -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        return True

    def _get_agent(
        self,
        *,
        model: Any,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ):
        signature = (model, max_tokens, temperature, timeout)
        if self._agent is not None and self._agent_signature == signature:
            return self._agent

        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings

        self._agent = Agent(
            model=model,
            output_type=SmartAskUserFieldSuggestion,
            instructions=SMART_ASK_USER_OPTION_INSTRUCTIONS,
            model_settings=ModelSettings(
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            ),
            retries=1,
            output_retries=1,
            defer_model_check=True,
        )
        self._agent_signature = signature
        return self._agent

    def _get_repair_followup_agent(
        self,
        *,
        model: Any,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ):
        signature = (model, max_tokens, temperature, timeout)
        if self._repair_followup_agent is not None and self._repair_followup_agent_signature == signature:
            return self._repair_followup_agent

        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings

        self._repair_followup_agent = Agent(
            model=model,
            output_type=RepairFollowupOptionSuggestion,
            instructions=REPAIR_FOLLOWUP_OPTION_INSTRUCTIONS,
            model_settings=ModelSettings(
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            ),
            retries=1,
            output_retries=1,
            defer_model_check=True,
        )
        self._repair_followup_agent_signature = signature
        return self._repair_followup_agent

    def _get_repair_followup_plan_agent(
        self,
        *,
        model: Any,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ):
        signature = (model, max_tokens, temperature, timeout)
        if self._repair_followup_plan_agent is not None and self._repair_followup_plan_agent_signature == signature:
            return self._repair_followup_plan_agent

        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings

        self._repair_followup_plan_agent = Agent(
            model=model,
            output_type=RepairFollowupPlanSuggestion,
            instructions=REPAIR_FOLLOWUP_PLAN_INSTRUCTIONS,
            model_settings=ModelSettings(
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            ),
            retries=1,
            output_retries=1,
            defer_model_check=True,
        )
        self._repair_followup_plan_agent_signature = signature
        return self._repair_followup_plan_agent

    def _normalize_repair_followup_prediction(
        self,
        prediction: RepairFollowupOptionSuggestion | None,
    ) -> list[str]:
        if prediction is None:
            return []

        labels: list[str] = []
        seen: set[str] = set()
        for item in prediction.options:
            label = str(item.label or "").strip()
            if not label or self._is_invalid_generated_option(label):
                continue
            normalized = label.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            labels.append(label)
        return labels[:MAX_REPAIR_FOLLOWUP_OPTIONS]

    def _normalize_repair_followup_plan_prediction(
        self,
        prediction: RepairFollowupPlanSuggestion | None,
    ) -> RepairFollowupPlanSuggestion | None:
        if prediction is None:
            return None

        allowed_keys = {
            "fault_phenomenon",
            "working_condition",
            "fault_codes",
            "ecu_or_system",
            "data_evidence",
            "repair_history",
        }
        normalized_fields: list[RepairFollowupFieldPlan] = []
        seen_keys: set[str] = set()

        for item in prediction.fields[:MAX_REPAIR_FOLLOWUP_FIELDS]:
            key = str(item.key or "").strip()
            if key not in allowed_keys or key in seen_keys:
                continue

            options: list[SmartAskUserCandidate] = []
            seen_labels: set[str] = set()
            for option in item.options:
                label = str(option.label or "").strip()
                if not label or self._is_invalid_generated_option(label):
                    continue
                normalized_label = label.lower()
                if normalized_label in seen_labels:
                    continue
                seen_labels.add(normalized_label)
                options.append(
                    SmartAskUserCandidate(
                        label=label,
                        description=str(option.description or "").strip() or None,
                    )
                )

            normalized_fields.append(
                RepairFollowupFieldPlan(
                    key=key,
                    label=str(item.label or "").strip() or None,
                    selection_mode=str(item.selection_mode or "").strip().lower() or None,
                    placeholder=str(item.placeholder or "").strip() or None,
                    hint=str(item.hint or "").strip() or None,
                    options=options[:MAX_REPAIR_FOLLOWUP_OPTIONS],
                )
            )
            seen_keys.add(key)

        if not normalized_fields:
            return None

        return RepairFollowupPlanSuggestion(
            ask_reason=str(prediction.ask_reason or "").strip() or None,
            fields=normalized_fields,
        )

    def _is_invalid_generated_option(self, label: str) -> bool:
        normalized = str(label or "").strip()
        if not normalized:
            return True
        if self.INVALID_OPTION_PATTERN.search(normalized):
            return True
        if self.PROCESS_OPTION_PATTERN.search(normalized):
            return True
        if self.LOW_INFORMATION_OPTION_PATTERN.search(normalized) and not self.DIAGNOSTIC_VALUE_PATTERN.search(normalized):
            return True
        return False


smart_ask_user_option_enricher = SmartAskUserOptionEnricher()


def to_form_options(options: list[SmartAskUserCandidate]) -> list[AskUserFormOption]:
    return [
        AskUserFormOption(
            key=item.label,
            label=item.label,
            description=item.description,
            option_source="llm_predicted",
            evidence_level="predicted",
        )
        for item in options
        if str(item.label or "").strip()
    ]
