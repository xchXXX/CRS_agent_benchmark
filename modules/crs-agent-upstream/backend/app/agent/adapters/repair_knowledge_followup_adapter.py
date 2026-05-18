"""Deterministic follow-up adapter for repair-knowledge answers."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from app.agent.ask_user_v2 import attach_form_to_ask_user
from app.agent.ask_user_v2.schema import (
    AskUserForm,
    AskUserFormAction,
    AskUserFormField,
    AskUserFormManualInput,
    AskUserFormOption,
    AskUserFormSection,
)
from app.agent.ask_user_v2.smart_option_enricher import (
    MAX_REPAIR_FOLLOWUP_FIELDS,
    smart_ask_user_option_enricher,
)
from app.agent.memory.deferred_store import DeferredState
from app.agent.models.ask_user import AskUserInputType, AskUserOption, AskUserQuestion


class RepairKnowledgeFollowupAdapter:
    """Convert missing-info repair answers into structured ask-user cards."""

    MAX_FIELD_GROUPS = MAX_REPAIR_FOLLOWUP_FIELDS
    MAX_FIELD_OPTIONS = 7

    DTC_PRESET_PATTERN = re.compile(r"^[PBUC][0-9A-Z]{4}\b", re.IGNORECASE)
    FAULT_CODE_STATUS_ONLY_PATTERN = re.compile(
        r"(?:^|[\s，,；;])(?:有明确故障码|故障灯亮但未读取具体报码|报码偶发|无报码|暂无故障码)(?:$|[\s，,；;])",
        re.IGNORECASE,
    )

    NORMALIZED_FIELD_KEYS = {
        "fault_codes",
        "data_evidence",
        "ecu_or_system",
        "working_condition",
        "fault_phenomenon",
        "repair_history",
    }
    CHOICE_FIRST_FIELD_KEYS = {
        "fault_codes",
        "data_evidence",
        "ecu_or_system",
        "working_condition",
        "fault_phenomenon",
        "repair_history",
    }
    FOLLOWUP_TEXT_PATTERNS = [
        r"还需补充",
        r"请补充",
        r"请提供",
        r"点击下方按钮",
        r"直接回复相关信息",
        r"上传数据流",
        r"数据流\s*csv",
        r"故障码列表",
    ]
    BUTTON_LINE_PATTERN = re.compile(r"^\[(?P<label>[^\[\]\n]{2,80})\]\s*$", re.MULTILINE)
    SUPPLEMENT_HEADER_PATTERN = re.compile(
        r"^\s{0,3}(?:#{1,6}\s*)?(输入信息|还需补充|补充信息|需要信息|需补充信息|请补充信息)\s*$",
        re.MULTILINE,
    )
    LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*]|[0-9]+[.、）)])\s*(?P<content>.+?)\s*$")
    META_REASONING_PATTERNS = [
        (
            re.compile(r"根据维修经验[，,：: ]*诊断的核心逻辑是区分“(?P<a>[^”]+)”与“(?P<b>[^”]+)”[。.]?"),
            r"先区分“\g<a>”与“\g<b>”。",
        ),
        (
            re.compile(r"诊断的核心逻辑是区分“(?P<a>[^”]+)”与“(?P<b>[^”]+)”[。.]?"),
            r"先区分“\g<a>”与“\g<b>”。",
        ),
        (
            re.compile(r"根据维修经验[，,：: ]*排查的核心逻辑是(?P<value>.+?)[。.]?"),
            r"先确认\g<value>。",
        ),
        (
            re.compile(r"排查的核心逻辑是(?P<value>.+?)[。.]?"),
            r"先确认\g<value>。",
        ),
    ]
    TRUST_ERODING_PREFIX_PATTERNS = [
        re.compile(r"^(?:由于|因)(?:缺乏|缺少|没有|暂无)(?:针对性的)?(?:维修案例|案例|资料|信息)[，,：: ]*"),
        re.compile(r"^(?:当前|目前|现阶段)(?:缺乏|缺少|没有|暂无)(?:针对性的)?(?:维修案例|案例|资料|信息)[，,：: ]*"),
        re.compile(r"^(?:当前|目前)(?:信息|资料|线索|证据)不足[，,：: ]*"),
        re.compile(r"^(?:现有|当前)(?:证据|线索|资料)(?:仍)?不足(?:以)?(?:稳定)?(?:判断|诊断|定位|给出结论)?[，,：: ]*"),
    ]
    TEXTUAL_INFO_REQUEST_PATTERNS = [
        re.compile(r"为了更(?:精准|准确|精确)(?:地)?(?:协助|判断|定位|诊断|排查)[^。！？\n]*?(?:请|建议).*(?:提供|补充).*$"),
        re.compile(r"^(?:请|建议)(?:提供|补充)更多(?:车辆)?(?:具体)?信息.*$"),
        re.compile(r"^(?:请|建议)(?:提供|补充)(?:您(?:的)?|车辆)?(?:相关)?信息.*$"),
        re.compile(r"^(?:如能|若能|如果方便)(?:继续)?(?:提供|补充).*(?:可|会|再).*(?:进一步|更)(?:精准|准确).*$"),
        re.compile(r"^(?:为了|为便于)(?:进一步|后续)(?:判断|定位|诊断|协助|排查)[^。！？\n]*?(?:请|建议).*(?:提供|补充).*$"),
        re.compile(r"^如果问题仍然无法解决[^。！？\n]*?(?:请|建议).*(?:提供|补充).*$"),
        re.compile(r"^如果(?:仍|还是)?无法(?:解决|定位|判断)[^。！？\n]*?(?:请|建议).*(?:提供|补充).*$"),
    ]
    FIELD_KEYWORDS = {
        "fault_codes": ("故障码", "报码", "报码情况"),
        "data_evidence": ("数据流", "轨压", "增压", "进气压力", "限扭", "压力", "跟随", "csv"),
        "ecu_or_system": ("ecu", "版本", "系统", "发动机电脑", "控制器", "品牌", "车型", "发动机型号", "型号", "机型", "吨位", "挖机", "挖掘机", "设备", "传感器", "模块", "支路", "回路"),
        "working_condition": ("工况", "急加速", "爬坡", "重载", "高速", "怠速", "冷车", "热车", "温度", "环境"),
        "fault_phenomenon": (
            "现象",
            "症状",
            "无力",
            "动力不足",
            "冒烟",
            "抖动",
            "熄火",
            "报码",
            "难启动",
            "难起动",
            "启动困难",
            "打不着火",
            "启动时间",
        ),
        "repair_history": ("维修", "更换", "保养", "处理过", "修过", "检修", "历史"),
    }
    DATA_STREAM_HINTS = ("轨压跟随", "进气压力", "增压压力", "限扭状态", "共轨压力", "油门开度")
    ELECTRICAL_REPAIR_HINTS = (
        "5v",
        "5伏",
        "供电",
        "供电短路",
        "参考电压",
        "基准电压",
        "拉低",
        "对地短路",
        "对正短路",
        "短路",
        "开路",
        "断路",
        "虚接",
        "掉电",
    )
    ECU_SYSTEM_ENTITY_CANDIDATES = (
        (("bcm", "车身"), "车身控制器BCM"),
        (("仪表",), "仪表/车身控制器"),
        (("发动机", "ecm", "ecu", "发动机电脑", "喷油"), "发动机控制器ECM"),
        (("变速箱", "tcu"), "变速箱控制器TCU"),
        (("abs", "ebs", "制动"), "ABS/EBS 控制器"),
        (("后处理", "scr", "尿素", "dcu"), "后处理控制器DCU"),
    )
    SENSOR_FAMILY_CANDIDATES = (
        (("踏板", "油门"), "油门踏板/位置类传感器"),
        (("轨压", "共轨压力", "进气压力", "空调压力", "压力"), "压力类传感器"),
        (("水温", "油温", "排温", "温度"), "温度类传感器"),
        (("曲轴", "凸轮", "位置", "转速"), "位置/转速类传感器"),
    )
    ELECTRICAL_SENSOR_GENERIC_CANDIDATES = (
        "发动机相关传感器/5V支路",
        "车身/底盘相关传感器/5V支路",
        "后处理相关传感器/5V支路",
        "油门踏板/位置类传感器",
        "压力/温度类传感器",
    )
    QUESTION_PREFIXES = (
        "是否",
        "有无",
        "是不是",
        "能否",
        "请问",
        "请先",
        "什么",
        "多少",
        "哪",
        "怎么",
        "如何",
        "为什么",
    )
    FIELD_PROMPT_HINTS = (
        "品牌",
        "型号",
        "发动机",
        "故障灯",
        "故障码",
        "报码",
        "温度",
        "现象",
        "工况",
        "ecu",
        "版本",
        "维修历史",
    )
    ANSWER_STYLE_HINTS = (
        "已知",
        "明确",
        "已上传",
        "暂无",
        "偶发",
        "明显",
        "持续",
        "同时出现",
        "不清楚",
        "正常",
        "异常",
        "报码偶发",
        "无报码",
        "限扭",
        "报码后",
        "报码且",
    )
    ACTION_STYLE_HINTS = (
        "上传",
        "协助",
        "分析",
        "排查",
        "继续",
        "诊断",
        "查看",
        "给我",
        "直接",
        "先给",
    )
    STARTING_ISSUE_HINTS = (
        "难启动",
        "难起动",
        "启动困难",
        "无法启动",
        "启动不了",
        "启动不上",
        "打不着火",
        "打不着",
        "起动机",
        "启动机",
        "启动时间长",
        "启动时间过长",
        "启动后熄火",
        "着车后熄火",
        "冷启动",
        "冷车难启动",
        "冷车难起动",
    )
    STARTER_MOTOR_HINTS = (
        "起动机",
        "启动机",
        "启动马达",
        "打钥匙无反应",
        "打火没反应",
        "点火没反应",
        "只听到咔哒",
        "只响一下",
        "吸合但不转",
        "空转",
    )
    COLD_START_HINTS = (
        "冷启动",
        "冷车",
        "低温",
        "气温低",
        "停放一夜",
        "早上首次启动",
    )
    HOT_START_HINTS = (
        "热车",
        "热启动",
        "熄火后再启动",
        "跑热后",
        "高温",
    )
    POWER_LOSS_HINTS = (
        "动力不足",
        "加速无力",
        "爬坡无力",
        "最高车速上不去",
        "限扭",
    )
    AIR_CONDITIONING_HINTS = (
        "空调",
        "制冷",
        "不制冷",
        "不凉",
        "出风不凉",
        "压缩机",
        "高低压",
        "压力表",
        "蒸发箱",
        "冷凝器",
        "冷凝风扇",
        "电子扇",
        "制冷剂",
    )
    COMMUNICATION_HINTS = (
        "j1939",
        "can",
        "通讯",
        "通信",
        "离线",
        "网络",
    )
    REPAIR_ACTION_HINTS = (
        "怎么办",
        "怎么查",
        "如何查",
        "怎么排查",
        "如何排查",
        "怎么检查",
        "如何检查",
        "怎么测",
        "如何测",
        "怎么量",
        "如何量",
        "怎么处理",
        "如何处理",
        "什么原因",
        "怎么修",
        "如何修",
        "排查",
        "诊断",
    )
    REPAIR_SIGNAL_HINTS = (
        "轨压",
        "共轨",
        "报码",
        "报码灯",
        "报码列表",
        "报码情况",
        "报码方向",
        "短路",
        "开路",
        "断路",
        "虚接",
        "对地短路",
        "对正短路",
        "搭铁不良",
        "供电异常",
        "供电短路",
        "参考电压",
        "基准电压",
    )
    LOCATION_LOOKUP_HINTS = (
        "检测口",
        "诊断口",
        "接口",
        "插口",
        "插头",
        "位置",
        "在哪里",
        "在哪",
    )
    ENGINEERING_MACHINE_HINTS = (
        "挖机",
        "挖掘机",
        "装载机",
        "吊车",
        "铲车",
        "工程机械",
    )
    INSTRUCTIONAL_PROMPT_HINTS = (
        "你已加载",
        "已加载本地维修知识库",
        "优先参考这些资料",
        "回答时优先参考",
        "共享上下文",
        "不要写",
        "不要调用",
        "不要暴露",
        "内部推理",
        "资料不足",
        "当前证据不足",
        "缺乏针对性的维修案例",
        "ask_user_question",
        "唯一允许的方式",
        "必须改为",
    )

    @classmethod
    def should_convert_to_followup(cls, text: str | None, loaded_context: dict[str, Any] | None) -> bool:
        if not text or not isinstance(loaded_context, dict) or not loaded_context.get("loaded"):
            return False
        stripped = text.strip()
        if not stripped:
            return False
        if any(re.search(pattern, stripped, re.IGNORECASE) for pattern in cls.FOLLOWUP_TEXT_PATTERNS):
            return True
        if cls.BUTTON_LINE_PATTERN.search(stripped):
            return True
        return False

    @classmethod
    def is_repair_followup_context(cls, context: dict[str, Any] | None) -> bool:
        return isinstance(context, dict) and (
            context.get("scene") == "repair_knowledge_followup"
            or context.get("card_type") == "repair_followup"
        )

    @classmethod
    def build_ask_user_question(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any],
        answer_text: str,
        tool_call_id: str | None = None,
    ) -> AskUserQuestion:
        field_groups = cls._build_field_groups(query=query, loaded_context=loaded_context, answer_text=answer_text)
        quick_actions = cls._extract_quick_actions(answer_text)
        ask_reason = cls._resolve_ask_reason(field_groups)
        field_groups_source = "rule"
        if any(str(group.get("option_source") or "").strip() == "llm_predicted" for group in field_groups):
            field_groups_source = "llm_plan"

        ask_user = AskUserQuestion(
            tool_call_id=tool_call_id or f"repair_followup_{uuid4().hex}",
            question="请先补充以下关键信息",
            input_type=AskUserInputType.TEXT,
            allow_free_input=True,
            input_hint="优先点选，若没有合适选项再手动补充",
            context={
                "scene": "repair_knowledge_followup",
                "card_type": "repair_followup",
                "ask_mode": "batch_once",
                "query": query,
                "repair_knowledge_query": query,
                "ask_reason": ask_reason,
                "source_refs": list(loaded_context.get("source_refs") or [])[:3],
                "field_groups": field_groups,
                "field_groups_source": field_groups_source,
                "quick_actions": quick_actions,
            },
        )
        return cls._attach_v2_form(ask_user=ask_user, field_groups=field_groups)

    @classmethod
    async def build_ask_user_question_async(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any],
        answer_text: str,
        tool_call_id: str | None = None,
    ) -> AskUserQuestion:
        field_groups = await cls._build_field_groups_async(
            query=query,
            loaded_context=loaded_context,
            answer_text=answer_text,
        )
        quick_actions = cls._extract_quick_actions(answer_text)
        ask_reason = cls._resolve_ask_reason(field_groups)
        field_groups_source = "rule"
        if any(str(group.get("option_source") or "").strip() == "llm_predicted" for group in field_groups):
            field_groups_source = "llm_plan"

        ask_user = AskUserQuestion(
            tool_call_id=tool_call_id or f"repair_followup_{uuid4().hex}",
            question="请先补充以下关键信息",
            input_type=AskUserInputType.TEXT,
            allow_free_input=True,
            input_hint="优先点选，若没有合适选项再手动补充",
            context={
                "scene": "repair_knowledge_followup",
                "card_type": "repair_followup",
                "ask_mode": "batch_once",
                "query": query,
                "repair_knowledge_query": query,
                "ask_reason": ask_reason,
                "source_refs": list(loaded_context.get("source_refs") or [])[:3],
                "field_groups": field_groups,
                "field_groups_source": field_groups_source,
                "quick_actions": quick_actions,
            },
        )
        return cls._attach_v2_form(ask_user=ask_user, field_groups=field_groups)

    @classmethod
    def normalize_ask_user_question(
        cls,
        ask_user: AskUserQuestion,
        *,
        query: str,
        loaded_context: dict[str, Any] | None = None,
    ) -> AskUserQuestion:
        context = dict(ask_user.context or {})
        if not cls.is_repair_followup_context(context):
            return ask_user

        normalized_query = str(
            query
            or context.get("repair_knowledge_query")
            or context.get("query")
            or ""
        ).strip()
        quick_actions = cls._normalize_quick_actions(context.get("quick_actions"), ask_user.options)
        raw_groups = list(context.get("field_groups") or [])
        field_groups_source = str(context.get("field_groups_source") or "").strip().lower()

        if field_groups_source != "llm_plan" and cls._should_rebuild_field_groups(raw_groups) and isinstance(loaded_context, dict) and loaded_context.get("loaded"):
            rebuilt = cls.build_ask_user_question(
                query=normalized_query,
                loaded_context=loaded_context,
                answer_text=str(loaded_context.get("llm_context") or ask_user.question or ""),
                tool_call_id=ask_user.tool_call_id,
            )
            rebuilt_context = dict(rebuilt.context or {})
            if quick_actions:
                rebuilt_context["quick_actions"] = quick_actions
            return rebuilt.model_copy(
                update={
                    "options": cls._build_quick_action_options(quick_actions),
                    "context": rebuilt_context,
                }
            )

        normalized_groups = cls._normalize_field_groups(
            raw_groups=raw_groups,
            query=normalized_query,
            loaded_context=loaded_context,
        )
        if not normalized_groups and isinstance(loaded_context, dict) and loaded_context.get("loaded"):
            normalized_groups = cls._build_field_groups(
                query=normalized_query,
                loaded_context=loaded_context,
                answer_text=str(loaded_context.get("llm_context") or ask_user.question or ""),
            )

        normalized_context = {
            **context,
            "scene": "repair_knowledge_followup",
            "card_type": "repair_followup",
            "ask_mode": context.get("ask_mode") or "batch_once",
            "query": normalized_query or str(context.get("query") or "").strip(),
            "repair_knowledge_query": normalized_query or str(context.get("repair_knowledge_query") or "").strip(),
            "field_groups": normalized_groups[:cls.MAX_FIELD_GROUPS],
            "field_groups_source": field_groups_source or ("llm_plan" if any(str(group.get("option_source") or "").strip() == "llm_predicted" for group in normalized_groups) else "rule"),
            "quick_actions": quick_actions,
        }
        normalized_ask_reason = cls._normalize_optional_text(context.get("ask_reason"))
        if normalized_ask_reason:
            normalized_context["ask_reason"] = normalized_ask_reason
        elif normalized_groups:
            normalized_context["ask_reason"] = cls._build_ask_reason(normalized_groups)

        question = "请先补充以下关键信息"
        normalized_ask_user = ask_user.model_copy(
            update={
                "question": question,
                "input_type": AskUserInputType.TEXT,
                "allow_free_input": True,
                "input_hint": ask_user.input_hint or "优先点选，若没有合适选项再手动补充",
                "options": cls._build_quick_action_options(quick_actions),
                "context": normalized_context,
            }
        )
        return cls._attach_v2_form(ask_user=normalized_ask_user, field_groups=normalized_groups[:cls.MAX_FIELD_GROUPS])

    @classmethod
    async def normalize_ask_user_question_async(
        cls,
        ask_user: AskUserQuestion,
        *,
        query: str,
        loaded_context: dict[str, Any] | None = None,
    ) -> AskUserQuestion:
        context = dict(ask_user.context or {})
        if not cls.is_repair_followup_context(context):
            return ask_user

        normalized_query = str(
            query
            or context.get("repair_knowledge_query")
            or context.get("query")
            or ""
        ).strip()
        quick_actions = cls._normalize_quick_actions(context.get("quick_actions"), ask_user.options)
        raw_groups = list(context.get("field_groups") or [])
        field_groups_source = str(context.get("field_groups_source") or "").strip().lower()

        if field_groups_source != "llm_plan" and cls._should_rebuild_field_groups(raw_groups) and isinstance(loaded_context, dict) and loaded_context.get("loaded"):
            rebuilt = await cls.build_ask_user_question_async(
                query=normalized_query,
                loaded_context=loaded_context,
                answer_text=str(loaded_context.get("llm_context") or ask_user.question or ""),
                tool_call_id=ask_user.tool_call_id,
            )
            rebuilt_context = dict(rebuilt.context or {})
            if quick_actions:
                rebuilt_context["quick_actions"] = quick_actions
            return rebuilt.model_copy(
                update={
                    "options": cls._build_quick_action_options(quick_actions),
                    "context": rebuilt_context,
                }
            )

        normalized_groups = await cls._normalize_field_groups_async(
            raw_groups=raw_groups,
            query=normalized_query,
            loaded_context=loaded_context,
        )
        if not normalized_groups and isinstance(loaded_context, dict) and loaded_context.get("loaded"):
            normalized_groups = await cls._build_field_groups_async(
                query=normalized_query,
                loaded_context=loaded_context,
                answer_text=str(loaded_context.get("llm_context") or ask_user.question or ""),
            )

        normalized_context = {
            **context,
            "scene": "repair_knowledge_followup",
            "card_type": "repair_followup",
            "ask_mode": context.get("ask_mode") or "batch_once",
            "query": normalized_query or str(context.get("query") or "").strip(),
            "repair_knowledge_query": normalized_query or str(context.get("repair_knowledge_query") or "").strip(),
            "field_groups": normalized_groups[:cls.MAX_FIELD_GROUPS],
            "field_groups_source": field_groups_source or ("llm_plan" if any(str(group.get("option_source") or "").strip() == "llm_predicted" for group in normalized_groups) else "rule"),
            "quick_actions": quick_actions,
        }
        normalized_ask_reason = cls._normalize_optional_text(context.get("ask_reason"))
        if normalized_ask_reason:
            normalized_context["ask_reason"] = normalized_ask_reason
        elif normalized_groups:
            normalized_context["ask_reason"] = cls._build_ask_reason(normalized_groups)

        question = "请先补充以下关键信息"
        normalized_ask_user = ask_user.model_copy(
            update={
                "question": question,
                "input_type": AskUserInputType.TEXT,
                "allow_free_input": True,
                "input_hint": ask_user.input_hint or "优先点选，若没有合适选项再手动补充",
                "options": cls._build_quick_action_options(quick_actions),
                "context": normalized_context,
            }
        )
        return cls._attach_v2_form(ask_user=normalized_ask_user, field_groups=normalized_groups[:cls.MAX_FIELD_GROUPS])

    @staticmethod
    def build_deferred_state(
        *,
        tool_call_id: str,
        message_history_json: str,
        query: str,
        ask_user: AskUserQuestion,
    ) -> DeferredState:
        return DeferredState(
            tool_call_id=tool_call_id,
            tool_name="ask_user_question",
            message_history_json=message_history_json,
            payload={
                "query": query,
                "synthetic_followup": True,
                "ask_user": ask_user.model_dump(mode="json"),
            },
        )

    @classmethod
    def _attach_v2_form(
        cls,
        *,
        ask_user: AskUserQuestion,
        field_groups: list[dict[str, Any]],
    ) -> AskUserQuestion:
        context = dict(ask_user.context or {})
        form = cls._build_v2_form(
            tool_call_id=ask_user.tool_call_id,
            ask_reason=str(context.get("ask_reason") or "").strip() or None,
            field_groups=field_groups,
            quick_actions=context.get("quick_actions"),
        )
        return attach_form_to_ask_user(
            ask_user,
            form=form,
            card_type=str(context.get("card_type") or "repair_followup"),
            scene="repair_knowledge_followup",
        )

    @classmethod
    def _build_v2_form(
        cls,
        *,
        tool_call_id: str,
        ask_reason: str | None,
        field_groups: list[dict[str, Any]],
        quick_actions: Any = None,
    ) -> AskUserForm:
        fields = [
            cls._build_v2_field(group=group, index=index)
            for index, group in enumerate(field_groups[:cls.MAX_FIELD_GROUPS])
            if isinstance(group, dict)
        ]
        form = AskUserForm(
            form_id=f"repair_followup_form_{tool_call_id}",
            title="维修问答补充",
            description="优先点选最接近的情况；没有合适项时再手动补充。",
            ask_reason=ask_reason,
            mode="progressive",
            sections=[
                AskUserFormSection(
                    id="core",
                    title="维修问答补充",
                    fields=fields,
                )
            ],
        )
        form.ui_policy.layout = "stepper"
        form.ui_policy.show_summary_preview = True
        form.ui_policy.submit_button_text = "继续分析"
        form.ui_policy.dense = True
        form.actions = cls._build_v2_actions(quick_actions)
        return form

    @classmethod
    def _build_v2_field(
        cls,
        *,
        group: dict[str, Any],
        index: int,
    ) -> AskUserFormField:
        key = str(group.get("key") or f"repair_field_{index}").strip() or f"repair_field_{index}"
        label = str(group.get("label") or cls._fallback_group_label(key=key, index=index)).strip() or cls._fallback_group_label(key=key, index=index)
        required_level = cls._normalize_required_level(group.get("required_level"), key=key)
        presets = cls._dedupe_presets(cls._extract_group_presets(group), limit=cls.MAX_FIELD_OPTIONS)
        option_source = str(group.get("option_source") or "rule").strip() or "rule"
        if not presets and key in cls.CHOICE_FIRST_FIELD_KEYS and option_source != "llm_predicted":
            presets = cls._minimal_choice_presets(key=key)
        has_presets = bool(presets)
        selection_mode = cls._normalize_selection_mode(
            group.get("selection_mode"),
            key=key,
            has_presets=has_presets,
            presets=presets,
        )
        is_multi = has_presets and selection_mode == "multi"
        manual_input = AskUserFormManualInput(
            enabled=True,
            always_visible=False,
            placeholder=cls._normalize_optional_text(group.get("placeholder")) or cls._placeholder_for_key(key),
            input_hint=cls._normalize_optional_text(group.get("hint")),
            value_type="code" if key == "fault_codes" else "text",
        )
        return AskUserFormField(
            key=key,
            label=label,
            field_type="multi_select" if is_multi else ("single_select" if has_presets else "text"),
            answer_mode="select_and_text" if is_multi else ("select_or_text" if has_presets else "text_only"),
            required=required_level == "hard",
            required_level=required_level,
            placeholder=cls._normalize_optional_text(group.get("placeholder")) or cls._placeholder_for_key(key),
            hint=cls._normalize_optional_text(group.get("hint")),
            options=[
                AskUserFormOption(
                    key=preset,
                    label=cls._option_label_for_preset(field_key=key, preset=preset),
                    option_source=option_source,
                    evidence_level="predicted" if option_source == "llm_predicted" else "confirmed",
                )
                for preset in presets
            ],
            manual_input=manual_input,
        )

    @classmethod
    def _option_label_for_preset(cls, *, field_key: str, preset: str) -> str:
        return preset

    @classmethod
    def _build_v2_actions(cls, raw_actions: Any) -> list[AskUserFormAction]:
        actions = cls._normalize_quick_actions(raw_actions, top_level_options=None)
        return [
            AskUserFormAction(
                key=str(item.get("key") or item.get("label") or "").strip(),
                label=str(item.get("label") or item.get("key") or "").strip(),
                description=str(item.get("description") or "").strip() or None,
                variant="ghost",
                action_type="quick_reply",
                payload={"quick_action": str(item.get("key") or item.get("label") or "").strip()},
            )
            for item in actions
            if str(item.get("key") or item.get("label") or "").strip()
            and str(item.get("label") or item.get("key") or "").strip()
        ]

    @classmethod
    def normalize_user_facing_message(cls, content: str) -> str:
        text = content.strip()
        if not text:
            return text

        lines: list[str] = []
        skip_followup_prompt_block = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if skip_followup_prompt_block:
                if not line:
                    skip_followup_prompt_block = False
                    continue
                if line.startswith("#"):
                    skip_followup_prompt_block = False
                elif cls._looks_like_textual_followup_item(line):
                    continue
                else:
                    skip_followup_prompt_block = False
            if not line:
                lines.append("")
                continue
            if re.search(r"您可以直接回复相关信息|点击下方按钮", line):
                continue
            if cls.BUTTON_LINE_PATTERN.fullmatch(line):
                continue

            normalized = line
            normalized, removed_followup_line, starts_followup_block = cls._strip_textual_followup_request(normalized)
            if removed_followup_line:
                skip_followup_prompt_block = starts_followup_block
            if not normalized.strip():
                continue
            for pattern, replacement in cls.META_REASONING_PATTERNS:
                normalized = pattern.sub(replacement, normalized)
            normalized = re.sub(r"^根据维修经验[，,：: ]*", "", normalized)
            normalized = re.sub(r"^通常的诊断思路是", "先", normalized)
            normalized = cls._strip_trust_eroding_prefix(normalized)
            normalized = normalized.strip("，,；;：: ")
            if not normalized:
                continue
            lines.append(normalized.strip())

        compacted = "\n".join(lines)
        compacted = re.sub(r"\n{3,}", "\n\n", compacted)
        return compacted.strip()

    @classmethod
    def _strip_trust_eroding_prefix(cls, text: str) -> str:
        normalized = text.strip()
        changed = True
        while changed and normalized:
            changed = False
            for pattern in cls.TRUST_ERODING_PREFIX_PATTERNS:
                updated = pattern.sub("", normalized, count=1).strip()
                if updated != normalized:
                    normalized = updated
                    changed = True
        return normalized

    @classmethod
    def _strip_textual_followup_request(cls, text: str) -> tuple[str, bool, bool]:
        normalized = text.strip()
        for pattern in cls.TEXTUAL_INFO_REQUEST_PATTERNS:
            match = pattern.search(normalized)
            if match is None:
                continue
            kept = normalized[:match.start()].rstrip("，,；;：: ")
            starts_followup_block = normalized.endswith(("：", ":")) or match.end() == len(normalized)
            return kept, True, starts_followup_block
        return normalized, False, False

    @classmethod
    def _looks_like_textual_followup_item(cls, text: str) -> bool:
        cleaned = cls._clean_item_text(text)
        if not cleaned:
            return False
        if cls._looks_like_field_prompt(cleaned):
            return True
        list_match = cls.LIST_ITEM_PATTERN.match(text)
        if list_match:
            item = cls._clean_item_text(list_match.group("content"))
            if item and (cls._looks_like_field_prompt(item) or cls._looks_like_needed_info(item)):
                return True
        lowered = cleaned.lower()
        return any(hint in lowered for hint in cls.FIELD_PROMPT_HINTS)

    @staticmethod
    def normalize_query_text(query: str | None) -> str:
        return str(query or "").replace("起动", "启动").replace("起動", "启动").strip()

    @classmethod
    def is_starting_issue_query(cls, query: str | None) -> bool:
        normalized = cls.normalize_query_text(query)
        return any(hint in normalized for hint in cls.STARTING_ISSUE_HINTS)

    @classmethod
    def is_repair_diagnosis_query(cls, query: str | None) -> bool:
        normalized = cls.normalize_query_text(query)
        if not normalized:
            return False

        lowered = normalized.lower()
        if cls.is_starting_issue_query(normalized):
            return True
        if any(hint in normalized for hint in cls.POWER_LOSS_HINTS):
            return True

        has_comm_signal = any(hint in lowered for hint in cls.COMMUNICATION_HINTS)
        has_repair_action = any(hint in normalized for hint in cls.REPAIR_ACTION_HINTS)
        has_repair_signal = any(hint in normalized for hint in cls.REPAIR_SIGNAL_HINTS)

        if has_comm_signal and (has_repair_action or "故障" in normalized or "异常" in normalized):
            return True
        if has_repair_signal and (has_repair_action or "故障" in normalized or "异常" in normalized):
            return True
        return False

    @classmethod
    def is_location_lookup_query(cls, query: str | None) -> bool:
        normalized = cls.normalize_query_text(query)
        if not normalized:
            return False
        return any(hint in normalized for hint in cls.LOCATION_LOOKUP_HINTS)

    @classmethod
    def _is_engineering_machine_query(cls, query: str | None) -> bool:
        normalized = cls.normalize_query_text(query)
        if not normalized:
            return False
        return any(hint in normalized for hint in cls.ENGINEERING_MACHINE_HINTS)

    @classmethod
    def _combined_context_text(cls, *, query: str, loaded_context: dict[str, Any] | None) -> str:
        parts = [cls.normalize_query_text(query)]
        if isinstance(loaded_context, dict):
            entry_text = cls.normalize_query_text(cls._joined_text(loaded_context))
            if entry_text:
                parts.append(entry_text)
            elif loaded_context.get("llm_context"):
                # `llm_context` may contain system-style instructions. Only fall back to it
                # when structured entry content is unavailable.
                parts.append(cls.normalize_query_text(str(loaded_context.get("llm_context") or "")))
        return "\n".join(part for part in parts if part).strip()

    @classmethod
    def _detect_starting_issue_profile(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> str | None:
        combined = cls._combined_context_text(query=query, loaded_context=loaded_context)
        if not combined:
            return None

        if any(hint in combined for hint in cls.STARTER_MOTOR_HINTS):
            return "starter_motor"
        if ("打钥匙" in combined or "点火" in combined) and any(
            hint in combined for hint in ("无反应", "没反应", "咔哒", "不转")
        ):
            return "starter_motor"
        if not cls.is_starting_issue_query(combined):
            return None
        if any(hint in combined for hint in cls.COLD_START_HINTS):
            return "cold_start"
        if any(hint in combined for hint in cls.HOT_START_HINTS):
            return "hot_start"
        return "generic_start"

    @classmethod
    def _is_electrical_sensor_query(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> bool:
        combined = cls._combined_context_text(query=query, loaded_context=loaded_context)
        if not combined:
            return False

        lowered = combined.lower()
        has_sensor_signal = any(token in combined for token in ("传感器", "支路", "回路")) or "5v" in lowered
        has_electrical_signal = any(hint in combined for hint in cls.ELECTRICAL_REPAIR_HINTS) or any(
            hint in lowered for hint in cls.ELECTRICAL_REPAIR_HINTS
        )
        has_repair_action = any(hint in combined for hint in cls.REPAIR_ACTION_HINTS)
        return (has_sensor_signal and has_electrical_signal) or (has_electrical_signal and has_repair_action)

    @classmethod
    def _is_air_conditioning_query(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> bool:
        combined = cls._combined_context_text(query=query, loaded_context=loaded_context)
        if not combined:
            return False
        lowered = combined.lower()
        return any(hint in combined for hint in cls.AIR_CONDITIONING_HINTS) or any(
            hint in lowered for hint in cls.AIR_CONDITIONING_HINTS
        )

    @classmethod
    def _build_electrical_system_candidates(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> list[str]:
        if not cls._is_electrical_sensor_query(query=query, loaded_context=loaded_context):
            return []

        combined = cls._combined_context_text(query=query, loaded_context=loaded_context)
        if not combined:
            return []

        lowered = combined.lower()
        candidates: list[str] = []

        for keywords, label in cls.ECU_SYSTEM_ENTITY_CANDIDATES:
            if any((keyword.lower() in lowered) if keyword.isascii() else (keyword in combined) for keyword in keywords):
                candidates.append(label)

        for keywords, label in cls.SENSOR_FAMILY_CANDIDATES:
            if any((keyword.lower() in lowered) if keyword.isascii() else (keyword in combined) for keyword in keywords):
                candidates.append(label)

        candidates.extend(cls.ELECTRICAL_SENSOR_GENERIC_CANDIDATES)

        return cls._dedupe_presets(candidates, limit=cls.MAX_FIELD_OPTIONS)

    @classmethod
    def _should_use_semantic_system_candidates(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> bool:
        if cls.is_location_lookup_query(query):
            return False
        if cls.is_starting_issue_query(query):
            return False
        if cls._is_engineering_machine_query(query):
            return False
        if cls._is_air_conditioning_query(query=query, loaded_context=loaded_context):
            return True
        if cls._is_electrical_sensor_query(query=query, loaded_context=loaded_context):
            return True

        combined = cls._combined_context_text(query=query, loaded_context=loaded_context).lower()
        if any(hint in combined for hint in cls.COMMUNICATION_HINTS):
            return True
        if any(hint in combined for hint in cls.POWER_LOSS_HINTS):
            return True
        if any(token in combined for token in ("scr", "尿素", "后处理", "nox", "dpf", "轨压", "共轨", "燃油压力", "增压", "涡轮", "进气压力")):
            return True
        return False

    @classmethod
    def _build_field_groups(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any],
        answer_text: str,
    ) -> list[dict[str, Any]]:
        llm_groups, llm_ask_reason = cls._build_field_groups_from_llm_plan(
            query=query,
            loaded_context=loaded_context,
            answer_text=answer_text,
        )
        if llm_groups:
            return cls._apply_llm_ask_reason(llm_groups, llm_ask_reason)

        raw_items = cls._extract_candidate_items(loaded_context=loaded_context, answer_text=answer_text)
        groups: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for item in raw_items:
            key = cls._infer_field_key(item)
            if key in seen_keys:
                continue
            groups.append(cls._build_group_for_key(key=key, label=item, query=query, loaded_context=loaded_context))
            seen_keys.add(key)
            if len(groups) >= 4:
                break

        for key, label in cls._fallback_group_specs(query=query):
            if len(groups) >= 4:
                break
            if key in seen_keys:
                continue
            groups.append(cls._build_group_for_key(key=key, label=label, query=query, loaded_context=loaded_context))
            seen_keys.add(key)

        return cls._sort_field_groups(groups=groups, query=query)[:cls.MAX_FIELD_GROUPS]

    @classmethod
    async def _build_field_groups_async(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any],
        answer_text: str,
    ) -> list[dict[str, Any]]:
        llm_groups, llm_ask_reason = await cls._build_field_groups_from_llm_plan_async(
            query=query,
            loaded_context=loaded_context,
            answer_text=answer_text,
        )
        if llm_groups:
            return cls._apply_llm_ask_reason(llm_groups, llm_ask_reason)

        raw_items = cls._extract_candidate_items(loaded_context=loaded_context, answer_text=answer_text)
        groups: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for item in raw_items:
            key = cls._infer_field_key(item)
            if key in seen_keys:
                continue
            groups.append(await cls._build_group_for_key_async(key=key, label=item, query=query, loaded_context=loaded_context))
            seen_keys.add(key)
            if len(groups) >= 4:
                break

        for key, label in cls._fallback_group_specs(query=query):
            if len(groups) >= 4:
                break
            if key in seen_keys:
                continue
            groups.append(await cls._build_group_for_key_async(key=key, label=label, query=query, loaded_context=loaded_context))
            seen_keys.add(key)

        return cls._sort_field_groups(groups=groups, query=query)[:cls.MAX_FIELD_GROUPS]

    @classmethod
    def _build_field_groups_from_llm_plan(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any] | None,
        answer_text: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        prediction = smart_ask_user_option_enricher.suggest_repair_followup_plan(
            query=cls._combined_context_text(query=query, loaded_context=loaded_context) or query,
            answer_text=answer_text,
            loaded_context=loaded_context,
        )
        return cls._coerce_llm_plan_to_field_groups(
            prediction=prediction,
            query=query,
            loaded_context=loaded_context,
        )

    @classmethod
    async def _build_field_groups_from_llm_plan_async(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any] | None,
        answer_text: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        prediction = await smart_ask_user_option_enricher.suggest_repair_followup_plan_async(
            query=cls._combined_context_text(query=query, loaded_context=loaded_context) or query,
            answer_text=answer_text,
            loaded_context=loaded_context,
        )
        return cls._coerce_llm_plan_to_field_groups(
            prediction=prediction,
            query=query,
            loaded_context=loaded_context,
        )

    @classmethod
    def _coerce_llm_plan_to_field_groups(
        cls,
        *,
        prediction: Any,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if prediction is None:
            return [], None

        groups: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for index, item in enumerate(list(getattr(prediction, "fields", []) or [])[:cls.MAX_FIELD_GROUPS]):
            key = str(getattr(item, "key", "") or "").strip()
            if key not in cls.NORMALIZED_FIELD_KEYS or key in seen_keys:
                continue

            raw_options = [str(getattr(option, "label", "") or "").strip() for option in (getattr(item, "options", []) or [])]
            sanitized_presets = cls._recover_presets(
                raw_presets=raw_options,
                key=key,
                query=query,
                loaded_context=loaded_context,
                strict_llm=smart_ask_user_option_enricher._resolve_model() not in {None, "test"},
            )

            groups.append(
                {
                    "key": key,
                    "label": cls._sanitize_group_label(
                        key=key,
                        label=str(getattr(item, "label", "") or "").strip() or cls._fallback_group_label(key=key, index=index),
                        index=index,
                        query=query,
                    ),
                    "required_level": cls._normalize_required_level(None, key=key),
                    "selection_mode": cls._normalize_selection_mode(
                        getattr(item, "selection_mode", None),
                        key=key,
                        has_presets=bool(sanitized_presets),
                        presets=sanitized_presets,
                    ),
                    "presets": sanitized_presets,
                    "placeholder": cls._normalize_optional_text(getattr(item, "placeholder", None))
                    or cls._placeholder_for_key(key, query=query),
                    "hint": cls._normalize_optional_text(getattr(item, "hint", None))
                    or cls._hint_for_key(key, query=query, loaded_context=loaded_context),
                    "priority": index,
                    "option_source": "llm_predicted",
                }
            )
            seen_keys.add(key)

        ask_reason = cls._normalize_optional_text(getattr(prediction, "ask_reason", None))
        return groups, ask_reason

    @staticmethod
    def _apply_llm_ask_reason(groups: list[dict[str, Any]], ask_reason: str | None) -> list[dict[str, Any]]:
        if not ask_reason or not groups:
            return groups
        enriched = list(groups)
        enriched[0] = {**enriched[0], "ask_reason_override": ask_reason}
        return enriched

    @classmethod
    def _resolve_ask_reason(cls, field_groups: list[dict[str, Any]]) -> str:
        for group in field_groups:
            candidate = cls._normalize_optional_text(group.get("ask_reason_override"))
            if candidate:
                return candidate
        return cls._build_ask_reason(field_groups)

    @classmethod
    def _normalize_field_groups(
        cls,
        *,
        raw_groups: list[dict[str, Any]],
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for index, raw_group in enumerate(raw_groups[:cls.MAX_FIELD_GROUPS]):
            if not isinstance(raw_group, dict):
                continue

            label = str(raw_group.get("label") or "").strip()
            raw_key = str(raw_group.get("key") or "").strip()
            key = raw_key if raw_key in cls.NORMALIZED_FIELD_KEYS else cls._infer_field_key(label or raw_key)
            if key in seen_keys:
                continue

            presets = cls._extract_group_presets(raw_group)
            if cls._looks_like_field_prompt_collection(group_key=key, values=presets):
                presets = []
            sanitized_presets = cls._recover_presets(
                raw_presets=presets,
                key=key,
                query=query,
                loaded_context=loaded_context,
            )

            groups.append(
                {
                    "key": key,
                    "label": cls._sanitize_group_label(
                        key=key,
                        label=label or cls._fallback_group_label(key=key, index=index),
                        index=index,
                        query=query,
                    ),
                    "required_level": cls._normalize_required_level(raw_group.get("required_level"), key=key),
                    "selection_mode": cls._normalize_selection_mode(
                        raw_group.get("selection_mode"),
                        key=key,
                        has_presets=bool(sanitized_presets),
                        presets=sanitized_presets,
                    ),
                    "presets": sanitized_presets,
                    "placeholder": cls._normalize_optional_text(raw_group.get("placeholder"))
                    or cls._placeholder_for_key(key, query=query),
                    "hint": cls._normalize_optional_text(raw_group.get("hint"))
                    or cls._hint_for_key(key, query=query, loaded_context=loaded_context),
                    "priority": raw_group.get("priority") if isinstance(raw_group.get("priority"), int) else None,
                    "option_source": str(raw_group.get("option_source") or "").strip() or None,
                    "ask_reason_override": cls._normalize_optional_text(raw_group.get("ask_reason_override")),
                }
            )
            seen_keys.add(key)

        return cls._sort_field_groups(groups=groups, query=query)[:cls.MAX_FIELD_GROUPS]

    @classmethod
    async def _normalize_field_groups_async(
        cls,
        *,
        raw_groups: list[dict[str, Any]],
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for index, raw_group in enumerate(raw_groups[:cls.MAX_FIELD_GROUPS]):
            if not isinstance(raw_group, dict):
                continue

            label = str(raw_group.get("label") or "").strip()
            raw_key = str(raw_group.get("key") or "").strip()
            key = raw_key if raw_key in cls.NORMALIZED_FIELD_KEYS else cls._infer_field_key(label or raw_key)
            if key in seen_keys:
                continue

            presets = cls._extract_group_presets(raw_group)
            if cls._looks_like_field_prompt_collection(group_key=key, values=presets):
                presets = []
            sanitized_presets = await cls._recover_presets_async(
                raw_presets=presets,
                key=key,
                query=query,
                loaded_context=loaded_context,
            )

            groups.append(
                {
                    "key": key,
                    "label": cls._sanitize_group_label(
                        key=key,
                        label=label or cls._fallback_group_label(key=key, index=index),
                        index=index,
                        query=query,
                    ),
                    "required_level": cls._normalize_required_level(raw_group.get("required_level"), key=key),
                    "selection_mode": cls._normalize_selection_mode(
                        raw_group.get("selection_mode"),
                        key=key,
                        has_presets=bool(sanitized_presets),
                        presets=sanitized_presets,
                    ),
                    "presets": sanitized_presets,
                    "placeholder": cls._normalize_optional_text(raw_group.get("placeholder"))
                    or cls._placeholder_for_key(key, query=query),
                    "hint": cls._normalize_optional_text(raw_group.get("hint"))
                    or cls._hint_for_key(key, query=query, loaded_context=loaded_context),
                    "priority": raw_group.get("priority") if isinstance(raw_group.get("priority"), int) else None,
                    "option_source": str(raw_group.get("option_source") or "").strip() or None,
                    "ask_reason_override": cls._normalize_optional_text(raw_group.get("ask_reason_override")),
                }
            )
            seen_keys.add(key)

        return cls._sort_field_groups(groups=groups, query=query)[:cls.MAX_FIELD_GROUPS]

    @classmethod
    def _sort_field_groups(
        cls,
        *,
        groups: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        priority_map = cls._field_priority_map(query=query)

        def sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
            explicit_priority = item.get("priority")
            if isinstance(explicit_priority, int):
                return (-1, explicit_priority, 0)
            key = str(item.get("key") or "").strip()
            presets = cls._extract_group_presets(item)
            required_level = str(item.get("required_level") or "").strip().lower()
            required_rank = {"hard": 0, "strong": 1, "soft": 2}.get(required_level, 3)
            return (
                0 if presets else 1,
                priority_map.get(key, 99),
                required_rank,
            )

        return sorted(groups, key=sort_key)

    @classmethod
    def _field_priority_map(cls, *, query: str) -> dict[str, int]:
        normalized_query = cls.normalize_query_text(query).lower()
        if any(hint in normalized_query for hint in cls.COMMUNICATION_HINTS):
            return {
                "fault_codes": 0,
                "fault_phenomenon": 1,
                "data_evidence": 2,
                "working_condition": 3,
                "ecu_or_system": 4,
                "repair_history": 5,
            }
        if cls.is_starting_issue_query(normalized_query):
            return {
                "fault_phenomenon": 0,
                "fault_codes": 1,
                "working_condition": 2,
                "data_evidence": 3,
                "ecu_or_system": 4,
                "repair_history": 5,
            }
        if "动力不足" in normalized_query:
            return {
                "fault_phenomenon": 0,
                "working_condition": 1,
                "fault_codes": 2,
                "data_evidence": 3,
                "ecu_or_system": 4,
                "repair_history": 5,
            }
        return {
            "fault_codes": 0,
            "fault_phenomenon": 1,
            "working_condition": 2,
            "data_evidence": 3,
            "ecu_or_system": 4,
            "repair_history": 5,
        }

    @classmethod
    def _extract_candidate_items(cls, *, loaded_context: dict[str, Any], answer_text: str) -> list[str]:
        values: list[str] = []
        values.extend(cls._extract_list_items(answer_text))
        values.extend(cls._extract_inline_candidate_items(answer_text))
        for entry in loaded_context.get("entries") or []:
            content = str(entry.get("content") or "")
            values.extend(cls._extract_list_items(content))
            values.extend(cls._extract_inline_candidate_items(content))

        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = cls._clean_item_text(value)
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(cleaned)
        return deduped

    @classmethod
    def _extract_inline_candidate_items(cls, text: str) -> list[str]:
        if not text:
            return []

        normalized = str(text).replace("起动", "启动").replace("起動", "启动")
        sentences = re.split(r"[。\n；;]", normalized)
        items: list[str] = []

        for sentence in sentences:
            stripped = sentence.strip()
            if not stripped:
                continue

            body = stripped
            matched = False
            for prefix in ("请先补充", "还需补充", "请补充", "请提供", "需要补充", "先确认", "确认", "补充", "提供"):
                if prefix in stripped:
                    body = stripped.split(prefix, 1)[1].strip("：:，, ")
                    matched = True
                    break
            if not matched and not any(keyword in stripped.lower() for keywords in cls.FIELD_KEYWORDS.values() for keyword in keywords):
                continue

            body = re.sub(r"^(?:以下|当前|相关|这些|其中)?(?:信息|内容)?", "", body).strip("：:，, ")
            protected_phrases = {
                "车辆品牌及发动机型号": "__repair_phrase_0__",
                "品牌及发动机型号": "__repair_phrase_1__",
                "故障灯/报码状态": "__repair_phrase_2__",
            }
            expanded = body
            for phrase, token in protected_phrases.items():
                expanded = expanded.replace(phrase, token)
            expanded = expanded.replace("以及", "、").replace("及", "、").replace("和", "、")
            for phrase, token in protected_phrases.items():
                expanded = expanded.replace(token, phrase)
            for part in re.split(r"[、，,]", expanded):
                cleaned = cls._clean_item_text(part)
                if not cleaned:
                    continue
                if cls._looks_like_needed_info(cleaned) or cls._looks_like_field_prompt(cleaned):
                    items.append(cleaned)

        return items

    @classmethod
    def _extract_list_items(cls, text: str) -> list[str]:
        if not text:
            return []

        lines = text.splitlines()
        items: list[str] = []
        in_section = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_section:
                    continue
                continue
            if cls.SUPPLEMENT_HEADER_PATTERN.match(stripped):
                in_section = True
                continue
            if in_section and stripped.startswith("#"):
                in_section = False
            match = cls.LIST_ITEM_PATTERN.match(stripped)
            if match and (in_section or cls._looks_like_needed_info(match.group("content"))):
                items.append(match.group("content"))
        return items

    @classmethod
    def _extract_group_presets(cls, group: dict[str, Any]) -> list[str]:
        for field_name in ("presets", "options", "example_options", "examples", "suggestions", "choices"):
            values = cls._coerce_string_list(group.get(field_name))
            if values:
                return values
        return []

    @classmethod
    def _looks_like_needed_info(cls, text: str) -> bool:
        if cls._looks_like_instructional_prompt(text):
            return False
        lowered = text.lower()
        return any(keyword in lowered for keywords in cls.FIELD_KEYWORDS.values() for keyword in keywords)

    @classmethod
    def _looks_like_field_prompt(cls, text: str) -> bool:
        normalized = cls._clean_item_text(text)
        if not normalized:
            return False
        if cls._looks_like_instructional_prompt(normalized):
            return False

        lowered = normalized.lower()
        if "?" in normalized or "？" in normalized:
            return True
        if normalized.startswith(cls.QUESTION_PREFIXES):
            return True
        if normalized.endswith(("吗", "呢")):
            return True
        if any(hint in lowered for hint in cls.FIELD_PROMPT_HINTS):
            if any(hint in normalized for hint in cls.ACTION_STYLE_HINTS):
                return False
            return not any(hint in normalized for hint in cls.ANSWER_STYLE_HINTS)
        return False

    @classmethod
    def _looks_like_field_prompt_collection(cls, *, group_key: str, values: list[str]) -> bool:
        cleaned_values = [cls._clean_item_text(value) for value in values if cls._clean_item_text(value)]
        if len(cleaned_values) < 2:
            return False

        prompt_like_count = sum(1 for value in cleaned_values if cls._looks_like_field_prompt(value))
        cross_field_count = sum(1 for value in cleaned_values if cls._infer_field_key(value) != group_key)
        return prompt_like_count >= 2 or cross_field_count >= max(2, len(cleaned_values) - 1)

    @classmethod
    def _sanitize_presets(
        cls,
        values: list[str],
        *,
        key: str,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> list[str]:
        del key, query, loaded_context
        deduped: list[str] = []
        seen: set[str] = set()

        for value in values:
            cleaned = cls._clean_item_text(value)
            if not cleaned or cls._looks_like_instructional_prompt(cleaned) or cls._looks_like_field_prompt(cleaned):
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(cleaned)

        return deduped[:5]

    @classmethod
    def _recover_presets(
        cls,
        *,
        raw_presets: list[str],
        key: str,
        query: str,
        loaded_context: dict[str, Any] | None,
        strict_llm: bool = False,
    ) -> list[str]:
        sanitized_presets = cls._sanitize_presets(
            raw_presets,
            key=key,
            query=query,
            loaded_context=loaded_context,
        )
        if sanitized_presets:
            return cls._finalize_presets_for_key(key=key, presets=sanitized_presets)

        rebuilt_presets = cls._build_presets(
            key=key,
            query=query,
            loaded_context=loaded_context,
        )
        if rebuilt_presets:
            return cls._finalize_presets_for_key(key=key, presets=rebuilt_presets)

        if strict_llm:
            return []

        if key in cls.CHOICE_FIRST_FIELD_KEYS:
            fallback_presets = cls._build_deterministic_presets(
                key=key,
                query=query,
                loaded_context=loaded_context,
            )
            if fallback_presets:
                return cls._finalize_presets_for_key(key=key, presets=fallback_presets)

        return []

    @classmethod
    async def _recover_presets_async(
        cls,
        *,
        raw_presets: list[str],
        key: str,
        query: str,
        loaded_context: dict[str, Any] | None,
        strict_llm: bool = False,
    ) -> list[str]:
        sanitized_presets = cls._sanitize_presets(
            raw_presets,
            key=key,
            query=query,
            loaded_context=loaded_context,
        )
        if sanitized_presets:
            return cls._finalize_presets_for_key(key=key, presets=sanitized_presets)

        rebuilt_presets = await cls._build_presets_async(
            key=key,
            query=query,
            loaded_context=loaded_context,
        )
        if rebuilt_presets:
            return cls._finalize_presets_for_key(key=key, presets=rebuilt_presets)

        if strict_llm:
            return []

        if key in cls.CHOICE_FIRST_FIELD_KEYS:
            fallback_presets = cls._build_deterministic_presets(
                key=key,
                query=query,
                loaded_context=loaded_context,
            )
            if fallback_presets:
                return cls._finalize_presets_for_key(key=key, presets=fallback_presets)

        return []

    @classmethod
    def _should_rebuild_field_groups(cls, raw_groups: list[dict[str, Any]]) -> bool:
        if not raw_groups:
            return True

        suspicious_groups = 0
        valid_groups = 0
        for raw_group in raw_groups[:cls.MAX_FIELD_GROUPS]:
            if not isinstance(raw_group, dict):
                continue
            label = str(raw_group.get("label") or "").strip()
            key = str(raw_group.get("key") or "").strip()
            effective_key = key if key in cls.NORMALIZED_FIELD_KEYS else cls._infer_field_key(label or key)
            presets = cls._extract_group_presets(raw_group)
            if label or presets:
                valid_groups += 1
            if cls._looks_like_field_prompt_collection(group_key=effective_key, values=presets):
                suspicious_groups += 1

        if valid_groups == 0:
            return True
        return suspicious_groups > 0 and suspicious_groups >= valid_groups

    @staticmethod
    def _clean_item_text(text: str) -> str:
        value = re.sub(r"^\s*(?:[-*]|[0-9]+[.、）)])\s*", "", text).strip()
        value = value.strip("：:;；。,.，")
        return value

    @classmethod
    def _looks_like_instructional_prompt(cls, text: str) -> bool:
        normalized = cls._clean_item_text(text)
        if not normalized:
            return False
        lowered = normalized.lower()
        return any(str(hint).lower() in lowered for hint in cls.INSTRUCTIONAL_PROMPT_HINTS)

    @classmethod
    def _infer_field_key(cls, label: str) -> str:
        lowered = label.lower()
        if any(keyword in lowered for keyword in cls.FIELD_KEYWORDS["ecu_or_system"]):
            return "ecu_or_system"
        for key, keywords in cls.FIELD_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return key
        return "fault_phenomenon"

    @classmethod
    def _build_group_for_key(
        cls,
        *,
        key: str,
        label: str,
        query: str,
        loaded_context: dict[str, Any],
    ) -> dict[str, Any]:
        presets = cls._build_presets(key=key, query=query, loaded_context=loaded_context)
        return {
            "key": key,
            "label": cls._normalize_group_label(key=key, label=label, query=query),
            "required_level": "hard" if key in {"fault_codes", "data_evidence", "working_condition"} else "strong",
            "selection_mode": cls._selection_mode_for_presets(key=key, presets=presets) if presets else "mixed",
            "presets": presets,
            "placeholder": cls._placeholder_for_key(key, query=query),
            "hint": cls._hint_for_key(key, query=query, loaded_context=loaded_context),
        }

    @classmethod
    async def _build_group_for_key_async(
        cls,
        *,
        key: str,
        label: str,
        query: str,
        loaded_context: dict[str, Any],
    ) -> dict[str, Any]:
        presets = await cls._build_presets_async(key=key, query=query, loaded_context=loaded_context)
        return {
            "key": key,
            "label": cls._normalize_group_label(key=key, label=label, query=query),
            "required_level": "hard" if key in {"fault_codes", "data_evidence", "working_condition"} else "strong",
            "selection_mode": cls._selection_mode_for_presets(key=key, presets=presets) if presets else "mixed",
            "presets": presets,
            "placeholder": cls._placeholder_for_key(key, query=query),
            "hint": cls._hint_for_key(key, query=query, loaded_context=loaded_context),
        }

    @staticmethod
    def _selection_mode_for_key(key: str) -> str:
        if key in {"fault_codes", "working_condition", "ecu_or_system", "fault_phenomenon"}:
            return "single"
        if key in {"data_evidence", "repair_history"}:
            return "multi"
        return "mixed"

    @classmethod
    def _normalize_group_label(cls, *, key: str, label: str, query: str = "") -> str:
        normalized = re.sub(r"\s+", " ", label).strip().strip("：:?？")
        normalized = re.sub(r"^(当前存在的|当前的|当前|请补充|请提供)", "", normalized).strip()
        normalized = re.sub(r"[（(](?:如有|若有|如果有|可选)[^）)]*[）)]", "", normalized).strip()
        normalized = re.sub(r"^(是否有报?|是否读取到|是否存在)", "", normalized).strip()
        query_lower = cls.normalize_query_text(query).lower()
        is_communication = any(token in query_lower for token in cls.COMMUNICATION_HINTS)
        if key == "fault_codes" and "故障灯" in normalized and "报码" in normalized:
            return "故障灯/报码状态"
        if key == "fault_codes" and any(token in normalized for token in ("故障码类别", "报码类别")):
            return "故障码情况"
        if key == "fault_codes" and any(token in normalized for token in ("故障码", "报码")):
            return "故障码情况"
        if key == "fault_codes" and normalized in {"故障码", "报码"}:
            return "故障码情况"
        if key == "fault_phenomenon" and (is_communication or "故障现象" in normalized):
            return "当前故障现象"
        if key == "working_condition" and normalized in {"工况", "出现条件"}:
            return "出现条件"
        if key == "ecu_or_system":
            if is_communication:
                return "涉及的系统或控制器"
            if cls._is_electrical_sensor_query(query=query, loaded_context=None):
                if normalized in {"相关信息", "设备相关信息", "车辆相关信息", "系统相关信息", "ecu 或系统信息", "ECU 或系统信息"}:
                    return "受影响的传感器/系统"
                if any(token in normalized for token in ("系统", "模块", "传感器", "支路", "回路")):
                    return "受影响的传感器/系统"
            if cls._is_engineering_machine_query(query):
                return "挖机型号或吨位"
            if cls.is_location_lookup_query(query) and normalized in {"相关信息", "设备相关信息", "车辆相关信息", "系统相关信息", "挖掘机相关信息"}:
                return "设备型号或系统信息"
        if key == "data_evidence" and is_communication:
            return "关键异常观测"
        if key == "data_evidence":
            if any(token in normalized for token in ("记录", "观测", "测量", "压力表", "数据流", "报码截图", "检查")):
                return "关键异常观测"
            if normalized in {"关键数据", "关键数据流", "关键证据"}:
                return "关键异常观测"
        return normalized

    @classmethod
    def _sanitize_group_label(cls, *, key: str, label: str, index: int, query: str = "") -> str:
        normalized = cls._normalize_group_label(key=key, label=label, query=query)
        if not normalized or cls._looks_like_instructional_prompt(normalized):
            return cls._fallback_group_label(key=key, index=index)
        return normalized

    @classmethod
    def _selection_mode_for_presets(cls, *, key: str, presets: list[str]) -> str:
        if key == "fault_codes" and any(cls.DTC_PRESET_PATTERN.match(str(item or "").strip()) for item in presets):
            return "multi"
        return cls._selection_mode_for_key(key)

    @classmethod
    def _finalize_presets_for_key(cls, *, key: str, presets: list[str]) -> list[str]:
        if key == "fault_codes":
            normalized = cls._sanitize_fault_code_presets(list(presets or []))
            has_specific_dtc = any(cls.DTC_PRESET_PATTERN.match(str(item or "").strip()) for item in normalized)
            if has_specific_dtc:
                normalized = [item for item in normalized if item not in cls._fault_code_status_candidates()]
            if normalized:
                return cls._dedupe_presets(normalized, limit=cls.MAX_FIELD_OPTIONS)
            return cls._fault_code_status_candidates()
        return cls._dedupe_presets(presets, limit=cls.MAX_FIELD_OPTIONS)

    @classmethod
    def _dedupe_presets(cls, values: list[str], *, limit: int = 5) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = cls._clean_item_text(value)
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(cleaned)
        return deduped[:limit]

    @staticmethod
    def _fault_code_status_candidates() -> list[str]:
        return [
            "暂未读取到具体报码",
            "当前无报码",
        ]

    @classmethod
    def _sanitize_fault_code_presets(cls, presets: list[str]) -> list[str]:
        sanitized: list[str] = []
        for item in presets or []:
            cleaned = cls._clean_item_text(item)
            if not cleaned:
                continue
            if cleaned in {"有明确故障码", "故障灯亮但未读取具体报码", "报码偶发", "无报码", "暂无故障码"}:
                continue
            sanitized.append(cleaned)
        return cls._dedupe_presets(sanitized, limit=cls.MAX_FIELD_OPTIONS)

    @classmethod
    def _merge_fault_code_status_presets(cls, presets: list[str]) -> list[str]:
        sanitized = cls._sanitize_fault_code_presets(list(presets or []))
        if sanitized:
            return sanitized
        return cls._fault_code_status_candidates()

    @classmethod
    def _query_has_fault_code_status_without_specific_code(cls, query: str) -> bool:
        normalized = cls.normalize_query_text(query)
        if not normalized:
            return False
        if cls.DTC_PRESET_PATTERN.search(normalized):
            return False
        return bool(cls.FAULT_CODE_STATUS_ONLY_PATTERN.search(normalized))

    @classmethod
    def _build_fault_code_candidates(
        cls,
        *,
        query: str,
        loaded_context: dict[str, Any] | None,
        profile: str | None,
        is_power_loss: bool,
        is_communication: bool,
    ) -> list[str]:
        combined = cls._combined_context_text(query=query, loaded_context=loaded_context)
        candidates: list[str] = []

        if profile == "cold_start":
            candidates.extend(
                [
                    "P0087 燃油轨压力过低",
                    "P0191 燃油轨压力传感器性能异常",
                    "P0335 曲轴位置传感器电路",
                    "P0340 凸轮轴位置传感器电路",
                    "P0380 预热系统故障",
                ]
            )
        elif profile == "hot_start":
            candidates.extend(
                [
                    "P0087 燃油轨压力过低",
                    "P0191 燃油轨压力传感器性能异常",
                    "P0335 曲轴位置传感器电路",
                    "P0340 凸轮轴位置传感器电路",
                    "P0560 系统电压异常",
                ]
            )
        elif profile == "starter_motor":
            candidates.extend(
                [
                    "P0615 起动继电器控制电路",
                    "P0513 防盗钥匙识别异常",
                    "P0562 系统电压过低",
                    "P0335 曲轴位置传感器电路",
                    "P0340 凸轮轴位置传感器电路",
                ]
            )
        elif profile is not None:
            candidates.extend(
                [
                    "P0615 起动继电器控制电路",
                    "P0562 系统电压过低",
                    "P0335 曲轴位置传感器电路",
                    "P0340 凸轮轴位置传感器电路",
                    "P0087 燃油轨压力过低",
                ]
            )

        lowered = combined.lower()
        if is_communication:
            candidates = [
                "U0100 与发动机控制模块通讯丢失",
                "U0101 与变速箱控制模块通讯丢失",
                "U0121 与ABS/EBS模块通讯丢失",
                "U0140 与车身/仪表控制模块通讯丢失",
                "U0073 控制模块通信总线关闭",
            ]
        elif is_power_loss:
            candidates = [
                "P0087 燃油轨压力过低",
                "P0299 增压压力过低",
                "P0101 空气流量/进气量信号异常",
                "P2263 增压系统性能故障",
                "P0401 EGR流量不足",
            ]

        if any(hint in combined for hint in ("轨压", "共轨", "燃油压力")):
            candidates = [
                "P0087 燃油轨压力过低",
                "P0191 燃油轨压力传感器性能异常",
                "P0251 喷油泵计量控制异常",
                "P0252 计量阀控制信号偏低",
                "P0093 燃油系统泄漏过大",
            ]
        elif any(hint in combined for hint in ("增压", "涡轮", "进气压力")):
            candidates = [
                "P0299 增压压力过低",
                "P2263 增压系统性能故障",
                "P0101 空气流量/进气量信号异常",
                "P0234 增压压力过高",
                "P0401 EGR流量不足",
            ]
        elif any(hint in lowered for hint in cls.COMMUNICATION_HINTS):
            candidates = [
                "U0100 与发动机控制模块通讯丢失",
                "U0101 与变速箱控制模块通讯丢失",
                "U0121 与ABS/EBS模块通讯丢失",
                "U0140 与车身/仪表控制模块通讯丢失",
                "U0073 控制模块通信总线关闭",
            ]

        return cls._dedupe_presets(candidates, limit=cls.MAX_FIELD_OPTIONS)

    @classmethod
    def _build_deterministic_presets(
        cls,
        *,
        key: str,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> list[str]:
        profile = cls._detect_starting_issue_profile(query=query, loaded_context=loaded_context)
        combined = cls._combined_context_text(query=query, loaded_context=loaded_context).lower()
        is_power_loss = any(hint in combined for hint in cls.POWER_LOSS_HINTS)
        is_communication = any(hint in combined for hint in cls.COMMUNICATION_HINTS)
        return cls._build_presets_without_smart_options(
            key=key,
            query=query,
            loaded_context=loaded_context,
            profile=profile,
            is_power_loss=is_power_loss,
            is_communication=is_communication,
        )

    @classmethod
    def _build_presets(cls, *, key: str, query: str, loaded_context: dict[str, Any] | None) -> list[str]:
        model_available = smart_ask_user_option_enricher._resolve_model() not in {None, "test"}
        if not model_available and key == "ecu_or_system" and cls.is_location_lookup_query(query):
            return smart_ask_user_option_enricher.suggest_model_option_labels(
                query=cls._combined_context_text(query=query, loaded_context=loaded_context) or query,
                input_hint=cls._hint_for_key(key, query=query, loaded_context=loaded_context),
                context={"scene": "repair_knowledge_followup"},
            )
        if not model_available and key == "ecu_or_system":
            combined = cls._combined_context_text(query=query, loaded_context=loaded_context).lower()
            if any(hint in combined for hint in cls.COMMUNICATION_HINTS):
                return [
                    "发动机控制器",
                    "变速箱控制器",
                    "ABS/EBS 控制器",
                    "仪表/车身控制器",
                    "后处理控制器",
                ]
            if cls.is_starting_issue_query(query):
                return ["东风", "解放", "重汽", "陕汽", "福田"]
        if model_available:
            predicted_options = smart_ask_user_option_enricher.suggest_repair_followup_option_labels(
                query=cls._combined_context_text(query=query, loaded_context=loaded_context) or query,
                field_key=key,
                field_label=cls._fallback_group_label(key=key, index=0),
                input_hint=cls._hint_for_key(key, query=query, loaded_context=loaded_context),
                loaded_context=loaded_context,
            )
            if predicted_options:
                return cls._finalize_presets_for_key(key=key, presets=predicted_options)
            return []

        return cls._build_deterministic_presets(
            key=key,
            query=query,
            loaded_context=loaded_context,
        )

    @classmethod
    async def _build_presets_async(
        cls,
        *,
        key: str,
        query: str,
        loaded_context: dict[str, Any] | None,
    ) -> list[str]:
        model_available = smart_ask_user_option_enricher._resolve_model() not in {None, "test"}
        if not model_available and key == "ecu_or_system" and cls.is_location_lookup_query(query):
            return await smart_ask_user_option_enricher.suggest_model_option_labels_async(
                query=cls._combined_context_text(query=query, loaded_context=loaded_context) or query,
                input_hint=cls._hint_for_key(key, query=query, loaded_context=loaded_context),
                context={"scene": "repair_knowledge_followup"},
            )
        if not model_available and key == "ecu_or_system":
            combined = cls._combined_context_text(query=query, loaded_context=loaded_context).lower()
            if any(hint in combined for hint in cls.COMMUNICATION_HINTS):
                return [
                    "发动机控制器",
                    "变速箱控制器",
                    "ABS/EBS 控制器",
                    "仪表/车身控制器",
                    "后处理控制器",
                ]
            if cls.is_starting_issue_query(query):
                return ["东风", "解放", "重汽", "陕汽", "福田"]
        if model_available:
            predicted_options = await smart_ask_user_option_enricher.suggest_repair_followup_option_labels_async(
                query=cls._combined_context_text(query=query, loaded_context=loaded_context) or query,
                field_key=key,
                field_label=cls._fallback_group_label(key=key, index=0),
                input_hint=cls._hint_for_key(key, query=query, loaded_context=loaded_context),
                loaded_context=loaded_context,
            )
            if predicted_options:
                return cls._finalize_presets_for_key(key=key, presets=predicted_options)
            return []

        return cls._build_deterministic_presets(
            key=key,
            query=query,
            loaded_context=loaded_context,
        )

    @classmethod
    def _build_presets_without_smart_options(
        cls,
        *,
        key: str,
        query: str,
        loaded_context: dict[str, Any] | None,
        profile: str | None,
        is_power_loss: bool,
        is_communication: bool,
    ) -> list[str]:
        is_air_conditioning = cls._is_air_conditioning_query(query=query, loaded_context=loaded_context)
        is_electrical_sensor = cls._is_electrical_sensor_query(query=query, loaded_context=loaded_context)
        combined = cls._combined_context_text(query=query, loaded_context=loaded_context)
        lowered = combined.lower()
        is_scr_related = any(token in lowered for token in ("scr", "尿素", "后处理", "nox", "dpf"))
        is_rail_pressure = any(token in combined for token in ("轨压", "共轨", "燃油压力"))
        if profile is not None:
            if key == "fault_phenomenon":
                if profile == "starter_motor":
                    return [
                        "打钥匙无反应",
                        "只听到咔哒声",
                        "起动机吸合但不转",
                        "起动机能转但发动机不着车",
                        "偶发无法启动",
                    ]
                if profile == "cold_start":
                    return [
                        "启动时间明显变长",
                        "起动机转速偏慢",
                        "起动机正常但不着车",
                        "着车后很快熄火",
                        "首次启动最明显",
                    ]
                if profile == "hot_start":
                    return [
                        "热车熄火后再启动困难",
                        "起动机正常但不着车",
                        "启动时间明显变长",
                        "着车后报码",
                        "偶发无法启动",
                    ]
                return [
                    "起动机无反应",
                    "起动机转速偏慢",
                    "起动机正常但不着车",
                    "着车后很快熄火",
                    "偶发无法启动",
                ]

            if key == "working_condition":
                if profile == "cold_start":
                    return [
                        "冷车明显",
                        "停放一夜后明显",
                        "低温时明显",
                        "热车后恢复正常",
                        "偶发出现",
                    ]
                if profile == "hot_start":
                    return [
                        "热车明显",
                        "熄火后短时间再启动明显",
                        "长时间停放后恢复正常",
                        "一直存在",
                        "偶发出现",
                    ]
                if profile == "starter_motor":
                    return [
                        "一直无法启动",
                        "偶发出现",
                        "冷车明显",
                        "热车明显",
                        "连续点火后更明显",
                    ]
                return [
                    "冷车明显",
                    "热车明显",
                    "一直无法启动",
                    "偶发出现",
                    "连续启动后更明显",
                ]

            if key == "fault_codes":
                return cls._build_fault_code_candidates(
                    query=query,
                    loaded_context=loaded_context,
                    profile=profile,
                    is_power_loss=False,
                    is_communication=False,
                )

            if key == "data_evidence":
                return [
                    "启动时电瓶电压",
                    "启动转速",
                    "轨压跟随",
                    "曲轴/凸轮轴同步状态",
                    "预热或起动继电器状态",
                ]

            return []

        if key == "fault_codes":
            specific_candidates = cls._build_fault_code_candidates(
                query=query,
                loaded_context=loaded_context,
                profile=None,
                is_power_loss=is_power_loss,
                is_communication=is_communication,
            )
            if specific_candidates and any(
                cls.DTC_PRESET_PATTERN.match(str(item or "").strip())
                for item in specific_candidates
            ):
                return cls._merge_fault_code_status_presets(specific_candidates)
            if is_communication:
                return cls._merge_fault_code_status_presets([
                    "U0100 与发动机控制模块通讯丢失",
                    "U0101 与变速箱控制模块通讯丢失",
                    "U0121 与ABS/EBS模块通讯丢失",
                    "U0140 与车身/仪表控制模块通讯丢失",
                    "U0073 控制模块通信总线关闭",
                ])
            if is_power_loss:
                return cls._merge_fault_code_status_presets([
                    "P0087 燃油轨压力过低",
                    "P0299 增压压力过低",
                    "P0101 空气流量/进气量信号异常",
                    "P2263 增压系统性能故障",
                    "P0401 EGR流量不足",
                ])
            return cls._merge_fault_code_status_presets([])

        if key == "working_condition":
            if is_air_conditioning:
                return [
                    "热车明显",
                    "怠速明显",
                    "高温暴晒后明显",
                    "跑起来稍好转",
                    "一直不制冷",
                ]
            if is_communication:
                return [
                    "通电后就报码",
                    "行驶中偶发",
                    "热车明显",
                    "颠簸后更明显",
                    "雨后或洗车后明显",
                ]
            if is_power_loss:
                return [
                    "急加速明显",
                    "爬坡明显",
                    "重载明显",
                    "一直存在",
                    "偶发出现",
                ]
            return [
                "冷车明显",
                "热车明显",
                "一直存在",
                "偶发出现",
            ]

        if key == "fault_phenomenon":
            if is_air_conditioning:
                return [
                    "风量正常但出风不凉",
                    "刚开始凉一会儿后变热",
                    "怠速不凉跑起来稍凉",
                    "一侧凉一侧不凉",
                    "压缩机频繁吸合",
                ]
            if is_communication:
                return [
                    "J1939 通讯中断",
                    "多个模块离线",
                    "仪表报码",
                    "车辆限扭",
                    "无法启动或熄火",
                ]
            if is_power_loss:
                return [
                    "加速无力",
                    "爬坡无力",
                    "最高车速上不去",
                    "伴随冒黑烟",
                    "伴随限扭",
                ]
            if is_rail_pressure:
                return [
                    "启动时轨压建立不上去",
                    "怠速轨压偏低",
                    "加速时轨压跟不上",
                    "热车后更明显",
                    "报码但动力暂时正常",
                ]
            if is_scr_related:
                return [
                    "排气管外壁/喷嘴处有结晶",
                    "无法建立尿素压力喷射",
                    "报码后限扭",
                    "尿素泵不工作",
                    "喷嘴喷射异常",
                ]
            if is_electrical_sensor:
                return [
                    "报码反复出现",
                    "报码清除后很快复现",
                    "相关传感器信号异常",
                    "供电 5V 被拉低",
                    "多个传感器同时异常",
                ]
            return [
                "报码或报码灯亮",
                "无法启动或易熄火",
                "动力不足或限扭",
                "异响/抖动/冒烟",
                "温度/压力/泄漏异常",
            ]

        if key == "data_evidence":
            if is_air_conditioning:
                return [
                    "空调相关故障码",
                    "高低压压力",
                    "出风口温度",
                    "压缩机工作状态",
                    "冷凝风扇工作状态",
                ]
            if is_communication:
                return [
                    "J1939 主干电阻",
                    "CAN_H/CAN_L 电压",
                    "终端电阻状态",
                    "模块在线状态",
                    "报码集中在通讯/离线类",
                ]
            if is_power_loss:
                return [
                    "轨压跟不上目标值",
                    "进气压力信号异常",
                    "增压压力明显偏低",
                    "限扭状态已激活",
                    "数据流暂未见明显异常",
                ]
            if is_air_conditioning:
                return [
                    "低压明显偏低",
                    "高压明显偏高",
                    "出风口温度降不下来",
                    "压缩机频繁吸合",
                    "冷凝风扇不工作",
                ]
            if profile is not None:
                return [
                    "启动时电瓶电压偏低",
                    "启动转速偏慢",
                    "轨压建立不上去",
                    "曲轴/凸轮轴同步异常",
                    "预热或起动继电器异常",
                ]
            return []

        if key == "ecu_or_system":
            if is_air_conditioning:
                return [
                    "空调控制系统",
                    "压缩机/电磁离合器回路",
                    "空调压力传感器/开关",
                    "冷凝风扇控制回路",
                    "鼓风/风门执行机构",
                ]
            if is_rail_pressure:
                return [
                    "高压共轨系统",
                    "低压供油系统",
                    "轨压传感器/计量阀回路",
                    "喷油器回油系统",
                    "发动机控制器 ECM",
                ]
            if is_scr_related:
                return [
                    "后处理控制器 DCU",
                    "尿素泵总成",
                    "尿素喷嘴/喷射管路",
                    "NOx/温度传感器链路",
                    "后处理线束与供电",
                ]
            return [
                "发动机系统",
                "后处理系统",
                "底盘/制动系统",
                "车身/空调系统",
                "仪表/网关系统",
            ]

        if key == "repair_history":
            if is_air_conditioning:
                return [
                    "近期补加/更换过制冷剂",
                    "近期更换过压缩机/皮带",
                    "近期清洗过冷凝器/蒸发箱",
                    "近期处理过风扇/风门",
                    "近期无相关维修",
                ]
            if is_communication:
                return [
                    "近期处理过线束/插头",
                    "近期更换过控制器模块",
                    "近期加装/改装过用电设备",
                    "近期做过搭铁/供电维修",
                    "近期无相关维修",
                ]
            if is_electrical_sensor:
                return [
                    "近期更换过相关传感器",
                    "近期处理过线束/插头",
                    "近期修过供电/搭铁",
                    "近期拆装过发动机/后处理部件",
                    "近期无相关维修",
                ]
            return [
                "近期更换过相关传感器/执行器",
                "近期处理过线束/插头",
                "近期拆装过相关系统部件",
                "近期清码或刷写过控制器",
                "近期无相关维修",
            ]

        return []

    @classmethod
    def _minimal_choice_presets(cls, *, key: str) -> list[str]:
        fallback_map = {
            "fault_codes": cls._fault_code_status_candidates(),
            "working_condition": ["冷车明显", "热车明显", "一直存在", "偶发出现"],
            "fault_phenomenon": ["报码或报码灯亮", "无法启动或易熄火", "动力不足或限扭", "异响/抖动/冒烟", "温度/压力异常"],
            "ecu_or_system": ["发动机系统", "后处理系统", "底盘/制动系统", "车身/空调系统", "仪表/网关系统"],
            "data_evidence": ["供电电压偏低", "压力/轨压偏低", "报码指向相关系统", "数据流跟随异常", "线束/插头存在虚接"],
            "repair_history": ["近期更换过相关部件", "近期处理过线束/插头", "近期拆装过相关系统", "近期清码/刷写过控制器", "近期无相关维修"],
        }
        return cls._dedupe_presets(list(fallback_map.get(key, [])), limit=cls.MAX_FIELD_OPTIONS)

    @classmethod
    def _placeholder_for_key(cls, key: str, *, query: str = "") -> str | None:
        profile = cls._detect_starting_issue_profile(query=query, loaded_context=None)
        normalized_query = cls.normalize_query_text(query).lower()
        is_communication = any(hint in normalized_query for hint in cls.COMMUNICATION_HINTS)
        if key == "ecu_or_system":
            if is_communication:
                return "例如：发动机控制器、仪表、EBS 或后处理控制器"
            if cls._is_electrical_sensor_query(query=query, loaded_context=None):
                return "例如：油门踏板、轨压传感器、空调压力传感器、BCM 相关支路"
            if cls._is_engineering_machine_query(query):
                return "例如：FR60E2-HD、FR150E2，或 20 吨级"
            if cls.is_location_lookup_query(query):
                return "例如：具体车型、设备型号或系统型号"
            return "例如：东风天龙 + 康明斯 ISZ13"
        if key == "fault_codes":
            return "例如：P0087、U0100；多个报码可直接补充"
        if key == "working_condition":
            if profile == "cold_start":
                return "例如：冷车停放一夜后明显，气温约 5℃"
            if profile == "hot_start":
                return "例如：热车熄火 10 分钟后最明显"
            return "例如：冷车明显、热车明显或一直无法启动"
        if key == "fault_phenomenon":
            if profile == "starter_motor":
                return "例如：打钥匙无反应、只听到咔哒声"
            return "例如：启动时间长、能转但不着车、着车后熄火"
        if key == "data_evidence":
            if cls._is_air_conditioning_query(query=query, loaded_context=None):
                return "例如：已测高低压，低压约 0.25MPa / 高压约 1.4MPa"
            return "例如：启动电压、启动转速、轨压跟随"
        if key == "repair_history":
            return "例如：近期换过电瓶、起动机或处理过线路"
        return None

    @classmethod
    def _hint_for_key(
        cls,
        key: str,
        *,
        query: str = "",
        loaded_context: dict[str, Any] | None = None,
    ) -> str | None:
        profile = cls._detect_starting_issue_profile(query=query, loaded_context=loaded_context)
        normalized_query = cls.normalize_query_text(query).lower()
        is_communication = any(hint in normalized_query for hint in cls.COMMUNICATION_HINTS)
        if key == "ecu_or_system":
            if is_communication:
                return "先选最可能受影响的系统或控制器；如果不确定，再手动补充模块名称。"
            if cls._is_electrical_sensor_query(query=query, loaded_context=loaded_context):
                return "先选最可能受影响的传感器或系统支路；不确定具体件名时，也可以先按系统范围点选。"
            if cls._is_engineering_machine_query(query):
                return "设备型号或吨位越准确，诊断口位置越容易定位。"
            if cls.is_location_lookup_query(query):
                return "具体型号、系统或吨位越准确，位置定位越稳定。"
            return "品牌、车系和发动机型号越完整，后续建议越准确。"
        if key == "fault_codes":
            if profile is not None:
                return "优先点选最接近的报码候选；如果还没读取报码，可直接选择“暂未读取到具体报码”；不在候选里时再手动补充。"
            return "优先点选最接近的报码候选；如果还没读取报码，可直接选择“暂未读取到具体报码”；不在候选里时再手动补充。"
        if key == "working_condition":
            if profile is not None:
                return "优先选择问题最稳定出现的条件，再补充温度、停放时长或复现规律。"
            return "先说明问题在什么工况下最容易复现。"
        if key == "fault_phenomenon":
            if profile == "starter_motor":
                return "优先选择和起动机当前反应最接近的一项。"
            if profile is not None:
                return "先选最接近的现象，再补充启动耗时、是否熄火或是否伴随报码。"
            return "只补充当前最明显的现象即可。"
        if key == "data_evidence":
            if cls._is_air_conditioning_query(query=query, loaded_context=loaded_context):
                return "优先选择最接近的异常结果，比如低压偏低、高压偏高、出风温度降不下来或风扇不工作。"
            return "优先选择最接近的异常结果、关键数值范围或正常/异常分型。"
        if key == "repair_history":
            return "近期更换过的零件或做过的维修，往往能直接缩小排查范围。"
        return None

    @classmethod
    def _fallback_group_specs(cls, *, query: str) -> list[tuple[str, str]]:
        normalized_query = cls.normalize_query_text(query)
        lowered_query = normalized_query.lower()
        if any(hint in lowered_query for hint in cls.COMMUNICATION_HINTS):
            return [
                ("fault_codes", "故障码情况"),
                ("fault_phenomenon", "当前故障现象"),
                ("data_evidence", "关键异常观测"),
                ("ecu_or_system", "涉及的系统或控制器"),
            ]
        if cls.is_location_lookup_query(normalized_query):
            if cls._is_engineering_machine_query(normalized_query):
                return [
                    ("ecu_or_system", "挖机型号或吨位"),
                ]
            return [
                ("ecu_or_system", "设备型号或系统信息"),
            ]
        if cls.is_starting_issue_query(normalized_query):
            return [
                ("ecu_or_system", "车辆品牌及发动机型号"),
                ("fault_codes", "是否亮故障灯或有故障码"),
                ("working_condition", "环境温度及出现条件"),
                ("fault_phenomenon", "具体难启动表现"),
            ]
        if cls._is_electrical_sensor_query(query=normalized_query, loaded_context=None):
            return [
                ("fault_codes", "故障码情况"),
                ("ecu_or_system", "受影响的传感器/系统"),
                ("repair_history", "近期维修/检查史"),
                ("data_evidence", "关键异常观测"),
            ]
        if cls._is_air_conditioning_query(query=normalized_query, loaded_context=None):
            return [
                ("fault_phenomenon", "当前空调现象"),
                ("working_condition", "不制冷最明显的工况"),
                ("data_evidence", "关键异常观测"),
                ("fault_codes", "当前空调相关报码"),
            ]
        if "动力不足" in normalized_query:
            return [
                ("fault_phenomenon", "当前动力不足表现"),
                ("working_condition", "出现动力不足的工况"),
                ("fault_codes", "当前故障码情况"),
                ("data_evidence", "关键异常观测"),
            ]
        return [
            ("fault_phenomenon", "当前故障现象"),
            ("working_condition", "出现问题的工况"),
            ("fault_codes", "当前故障码情况"),
        ]

    @classmethod
    def _extract_quick_actions(cls, answer_text: str) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        seen: set[str] = set()
        for match in cls.BUTTON_LINE_PATTERN.finditer(answer_text or ""):
            label = match.group("label").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            actions.append(
                {
                    "key": label,
                    "label": label,
                    "description": "作为快捷入口直接继续当前诊断。",
                }
            )
        return actions[:3]

    @classmethod
    def _normalize_quick_actions(
        cls,
        raw_actions: Any,
        top_level_options: list[AskUserOption] | None,
    ) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        seen: set[str] = set()

        def append_action(key: str, label: str, description: str | None = None) -> None:
            normalized_key = key.strip()
            normalized_label = label.strip()
            if not normalized_key or not normalized_label:
                return
            if cls._looks_like_field_prompt(normalized_label):
                return
            if normalized_key in seen:
                return
            seen.add(normalized_key)
            action = {"key": normalized_key, "label": normalized_label}
            if description:
                action["description"] = description.strip()
            actions.append(action)

        if isinstance(raw_actions, list):
            for item in raw_actions:
                if isinstance(item, str):
                    append_action(item, item)
                    continue
                if not isinstance(item, dict):
                    continue
                append_action(
                    str(item.get("key") or item.get("label") or ""),
                    str(item.get("label") or item.get("key") or ""),
                    str(item.get("description") or "").strip() or None,
                )

        for option in top_level_options or []:
            append_action(
                str(option.key or option.label or ""),
                str(option.label or option.key or ""),
                option.description,
            )

        return actions[:3]

    @staticmethod
    def _build_quick_action_options(actions: list[dict[str, str]]) -> list[AskUserOption]:
        return [
            AskUserOption(
                key=str(item.get("key") or item.get("label") or ""),
                label=str(item.get("label") or item.get("key") or ""),
                description=str(item.get("description") or "").strip() or None,
            )
            for item in actions
            if str(item.get("key") or item.get("label") or "").strip()
            and str(item.get("label") or item.get("key") or "").strip()
        ]

    @staticmethod
    def _build_ask_reason(field_groups: list[dict[str, Any]]) -> str:
        labels = [str(item.get("label") or "") for item in field_groups if item.get("label")]
        if not labels:
            return "还缺少关键现场信息，暂时无法继续缩小范围。"
        joined = "、".join(labels[:3])
        return f"还缺少 {joined} 等关键信息，补充后才能继续缩小范围。"

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []

        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
                continue
            if isinstance(item, dict):
                text = str(item.get("label") or item.get("key") or "").strip()
                if text:
                    result.append(text)
        return result

    @staticmethod
    def _normalize_optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if RepairKnowledgeFollowupAdapter._looks_like_instructional_prompt(text):
            return None
        return text or None

    @classmethod
    def _normalize_required_level(cls, value: Any, *, key: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"hard", "strong", "soft"}:
            return normalized
        return "hard" if key in {"fault_codes", "data_evidence", "working_condition"} else "strong"

    @classmethod
    def _normalize_selection_mode(cls, value: Any, *, key: str, has_presets: bool, presets: list[str]) -> str:
        normalized = str(value or "").strip().lower()
        if not has_presets:
            return "mixed"
        if normalized in {"single", "multi", "mixed"}:
            return normalized
        return cls._selection_mode_for_presets(key=key, presets=presets)

    @staticmethod
    def _fallback_group_label(*, key: str, index: int) -> str:
        fallback_map = {
            "fault_codes": "当前故障码情况",
            "data_evidence": "关键异常观测",
            "ecu_or_system": "ECU 或系统信息",
            "working_condition": "出现问题的工况",
            "fault_phenomenon": "当前故障现象",
            "repair_history": "近期维修历史",
        }
        return fallback_map.get(key, f"补充信息{index + 1}")

    @staticmethod
    def _joined_text(loaded_context: dict[str, Any]) -> str:
        entries = loaded_context.get("entries") or []
        return "\n".join(str(entry.get("content") or "") for entry in entries)
