import asyncio

from openpyxl import Workbook
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.adapters.repair_knowledge_followup_adapter import RepairKnowledgeFollowupAdapter
from app.agent.ask_user_v2.smart_option_enricher import (
    RepairFollowupFieldPlan,
    RepairFollowupPlanSuggestion,
    SmartAskUserCandidate,
    smart_ask_user_option_enricher,
)
from app.agent.context import CaseContextStore
from app.agent.domain.repair_knowledge.review import review_repair_answer_gate
from app.agent.domain.repair_knowledge import RepairKnowledgeService
from app.agent.memory.deferred_store import DeferredState, DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.models.events import AgentEventType
from app.agent.models.ask_user import AskUserInputType, AskUserQuestion
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.service import AgentLoopService
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import Settings
from app.schemas.chat import AskUserAnswer, ChatRequest


def build_repair_workbook(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "decoder_sit_ai_knowledge"
    sheet.append(["维修知识标题", "内容"])
    sheet.append(
        [
            "康明斯动力不足故障诊断分析提示词",
            "### 输入信息\n1. 当前车辆故障码列表\n2. 急加速工况下的动力相关数据流\n3. ECU版本信息\n",
        ]
    )
    sheet.append(
        [
            "康明斯动力不足选择数据流的提示词",
            "重点关注轨压跟随、进气压力、增压压力、限扭状态等关键数据流。",
        ]
    )
    sheet.append(
        [
            "**动力不足**通用提示词",
            "先看进气、轨压、限扭状态，再逐步排查油路和气路。",
        ]
    )
    workbook.save(path)


def build_test_deps(tmp_path, knowledge_path) -> AgentRuntimeDeps:
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        case_context_store=CaseContextStore(base_dir=str(tmp_path / "case_context")),
        repair_knowledge_service=RepairKnowledgeService(knowledge_path),
    )


def extract_request_text(messages: list[ModelMessage]) -> str:
    request = messages[-1]
    assert isinstance(request, ModelRequest)
    return "\n".join(
        part.content
        for part in request.parts
        if isinstance(getattr(part, "content", None), str)
    )


async def collect_stream_events(service: AgentLoopService, request: ChatRequest):
    return [event async for event in service.stream(request)]


def build_repair_followup_answer(tool_call_id: str, *, fields: dict, summary_text: str) -> AskUserAnswer:
    return AskUserAnswer(
        tool_call_id=tool_call_id,
        answer={
            "scene": "repair_knowledge_followup",
            "action": "submit",
            "fields": fields,
            "summary_text": summary_text,
        },
    )


def build_start_issue_loaded_context():
    return {
        "loaded": True,
        "source_refs": [{"id": "repair_knowledge_start", "title": "冷启动困难排查思路", "relation": "primary"}],
        "entries": [
            {
                "title": "冷启动困难排查思路",
                "content": "先确认车辆品牌及发动机型号、是否报码、环境温度以及具体难启动表现。",
            }
        ],
        "llm_context": "冷启动排查前，先确认车辆品牌及发动机型号、故障灯/报码状态、环境温度和具体难启动表现。",
    }


def build_starter_issue_loaded_context():
    return {
        "loaded": True,
        "source_refs": [{"id": "repair_knowledge_starter", "title": "启动系统排查思路", "relation": "primary"}],
        "entries": [
            {
                "title": "启动系统排查思路",
                "content": "先确认车辆品牌及发动机型号、报码状态、出现条件以及起动机具体反应。",
            }
        ],
        "llm_context": "启动系统排查前，先确认车辆品牌及发动机型号、故障灯/报码状态、出现条件和起动机具体反应。",
    }


def test_repair_knowledge_service_returns_title_catalog(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    service = RepairKnowledgeService(workbook_path)

    result = service.lookup_titles("康明斯动力不足怎么办")

    assert result["status"] == "ok"
    assert result["data"]["decision_mode"] == "llm_must_decide_match"
    assert result["data"]["title_count"] == 3
    assert result["data"]["recommended_titles"]
    assert result["data"]["recommended_titles"][0]["title"].startswith("康明斯动力不足")
    assert any(item["title"] == "**动力不足**通用提示词" for item in result["data"]["titles"])


def test_repair_knowledge_service_loads_selected_context_bundle(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    service = RepairKnowledgeService(workbook_path)

    result = service.load_context(["repair_knowledge_2"])

    assert result["status"] == "ok"
    assert result["data"]["loaded"] is True
    assert result["data"]["primary_source"]["title"] == "康明斯动力不足故障诊断分析提示词"
    assert result["data"]["entries"][0]["title"] == "康明斯动力不足故障诊断分析提示词"
    assert any(item["title"] == "康明斯动力不足选择数据流的提示词" for item in result["data"]["entries"])
    assert "**动力不足**通用提示词" not in result["data"]["llm_context"]


def test_agent_loop_service_attaches_repair_knowledge_sources(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def repair_knowledge_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            prompt_text = extract_request_text(messages)
            assert prompt_text == "康明斯动力不足怎么办"
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_knowledge_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            assert tool_return.content["data"]["title_count"] == 3
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_knowledge_2",
                    )
                ]
            )

        assert tool_return.tool_name == "get_repair_knowledge_context"
        assert tool_return.content["data"]["loaded"] is True
        return ModelResponse(parts=[TextPart(content="先重点看限扭状态、轨压跟随和进气压力。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(repair_knowledge_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)
    service._repair_gate_agent = None
    service._repair_renderer_agent = None

    response = asyncio.run(service.process(ChatRequest(message="康明斯动力不足怎么办")))

    assert response.type == "message"
    assert response.business == "GENERAL_CHAT"
    assert response.metadata["repair_knowledge_sources"][0]["title"] == "康明斯动力不足故障诊断分析提示词"


def test_agent_loop_service_strips_repair_knowledge_preamble_before_markdown_heading(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def repair_knowledge_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_knowledge_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_knowledge_2",
                    )
                ]
            )

        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "以下是针对您这个问题的初步诊断建议：\n"
                        "### 初步判断\n"
                        "先看限扭状态。\n\n"
                        "### 维修建议\n"
                        "优先检查轨压跟随和进气压力。"
                    )
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(repair_knowledge_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)
    service._repair_gate_agent = None
    service._repair_renderer_agent = None

    response = asyncio.run(service.process(ChatRequest(message="康明斯动力不足怎么办")))

    assert response.type == "message"
    assert isinstance(response.content, str)
    assert response.content.startswith("### 初步判断")
    assert "以下是针对您这个问题的初步诊断建议" not in response.content


def test_agent_loop_service_converts_repair_missing_info_text_to_ask_user(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def repair_knowledge_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_knowledge_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_knowledge_2",
                    )
                ]
            )

        assert tool_return.tool_name == "get_repair_knowledge_context"
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "### 初步判断\n"
                        "先重点看限扭状态和轨压跟随。\n\n"
                        "### 还需补充\n"
                        "请先补充故障码、动力相关数据流和 ECU 版本信息。\n\n"
                        "您可以直接回复相关信息，或点击下方按钮进行操作：\n"
                        "[上传数据流 CSV 文件进行诊断]\n"
                        "[我已经读取到故障码，请协助分析]"
                    )
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(repair_knowledge_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)
    service._repair_gate_agent = None
    service._repair_renderer_agent = None

    response = asyncio.run(service.process(ChatRequest(message="康明斯动力不足怎么办")))

    assert response.type == "ask_user"
    assert response.business == "GENERAL_CHAT"
    assert response.ask_user is not None
    assert response.ask_user.context["scene"] == "repair_knowledge_followup"
    assert response.ask_user.question == "请先补充以下关键信息"
    field_groups = response.ask_user.context["field_groups"]
    group_map = {group["key"]: group for group in field_groups}
    assert "fault_codes" in group_map
    assert "data_evidence" in group_map
    assert "ecu_or_system" in group_map
    assert any(item.startswith("P") for item in group_map["fault_codes"]["presets"])
    assert 1 <= len(group_map["data_evidence"]["presets"]) <= RepairKnowledgeFollowupAdapter.MAX_FIELD_OPTIONS
    assert 1 <= len(group_map["ecu_or_system"]["presets"]) <= RepairKnowledgeFollowupAdapter.MAX_FIELD_OPTIONS
    flattened_presets = group_map["data_evidence"]["presets"] + group_map["ecu_or_system"]["presets"]
    assert not any("上传" in item or "截图" in item or "文件" in item for item in flattened_presets)
    assert not any(
        "已确认" in item or "已核对" in item or "已记录" in item or "已查看" in item or "已测" in item
        for item in flattened_presets
    )
    quick_actions = response.ask_user.context["quick_actions"]
    assert quick_actions[0]["label"] == "上传数据流 CSV 文件进行诊断"
    assert quick_actions[1]["label"] == "我已经读取到故障码，请协助分析"
    assert deps.deferred_state_store.load(response.session_id, response.ask_user.tool_call_id) is not None


def test_repair_followup_adapter_builds_start_issue_groups(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    loaded_context = build_start_issue_loaded_context()

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query="冷车难起动怎么办",
        loaded_context=loaded_context,
        answer_text=loaded_context["llm_context"],
    )

    field_groups = ask_user.context["field_groups"]
    assert [group["label"] for group in field_groups] == [
        "具体难启动表现",
        "故障灯/报码状态",
        "环境温度",
        "车辆品牌及发动机型号",
    ]
    group_map = {group["key"]: group for group in field_groups}
    assert group_map["ecu_or_system"]["presets"] == [
        "东风",
        "解放",
        "重汽",
        "陕汽",
        "福田",
    ]
    assert any(item.startswith("P") for item in group_map["fault_codes"]["presets"])
    assert group_map["working_condition"]["presets"] == [
        "冷车明显",
        "停放一夜后明显",
        "低温时明显",
        "热车后恢复正常",
        "偶发出现",
    ]
    assert group_map["fault_phenomenon"]["presets"] == [
        "启动时间明显变长",
        "起动机转速偏慢",
        "起动机正常但不着车",
        "着车后很快熄火",
        "首次启动最明显",
    ]
    assert group_map["fault_codes"]["selection_mode"] == "multi"
    assert group_map["fault_codes"]["hint"] == "优先点选最接近的报码候选；如果还没读取报码，可直接选择“暂未读取到具体报码”；不在候选里时再手动补充。"


def test_repair_followup_adapter_builds_starter_issue_groups(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    loaded_context = build_starter_issue_loaded_context()

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query="起动机启动不了怎么办",
        loaded_context=loaded_context,
        answer_text=loaded_context["llm_context"],
    )

    group_map = {group["key"]: group for group in ask_user.context["field_groups"]}
    assert any(item.startswith("P") for item in group_map["fault_codes"]["presets"])
    assert "暂未读取到具体报码" not in group_map["fault_codes"]["presets"]
    assert "当前无报码" not in group_map["fault_codes"]["presets"]
    assert group_map["working_condition"]["presets"] == [
        "一直无法启动",
        "偶发出现",
        "冷车明显",
        "热车明显",
        "连续点火后更明显",
    ]
    assert group_map["fault_phenomenon"]["presets"] == [
        "打钥匙无反应",
        "只听到咔哒声",
        "起动机吸合但不转",
        "起动机能转但发动机不着车",
        "偶发无法启动",
    ]
    assert ask_user.context["schema_version"] == "2.0"
    form = ask_user.context["form"]
    assert form["mode"] == "progressive"
    assert form["ui_policy"]["layout"] == "stepper"
    assert [field["key"] for field in form["sections"][0]["fields"]] == [
        "fault_phenomenon",
        "fault_codes",
        "working_condition",
        "ecu_or_system",
    ]
    assert next(field for field in form["sections"][0]["fields"] if field["key"] == "fault_codes")["field_type"] == "multi_select"


def test_repair_followup_adapter_builds_generic_fault_code_presets(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    loaded_context = {
        "loaded": True,
        "source_refs": [{"id": "repair_knowledge_generic", "title": "通用报码排查思路", "relation": "primary"}],
        "entries": [
            {
                "title": "通用报码排查思路",
                "content": "先确认当前故障码情况和报码是否稳定出现。",
            }
        ],
        "llm_context": "先确认当前故障码情况和报码是否稳定出现。",
    }

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query="报码怎么分析",
        loaded_context=loaded_context,
        answer_text=loaded_context["llm_context"],
    )

    group_map = {group["key"]: group for group in ask_user.context["field_groups"]}
    assert group_map["fault_codes"]["label"] == "故障码情况"
    assert group_map["fault_codes"]["presets"] == [
        "暂未读取到具体报码",
        "当前无报码",
    ]


def test_repair_followup_normalization_fills_presets_without_loaded_context(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    ask_user = AskUserQuestion(
        tool_call_id="repair_followup_test",
        question="请补充起动机故障的具体信息及车辆信息，以便为您提供准确的排查建议。",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={
            "scene": "repair_knowledge_followup",
            "card_type": "repair_followup",
            "query": "起动机启动不了怎么办",
            "field_groups": [
                {
                    "key": "fault_codes",
                    "label": "相关故障码（如有）",
                    "required_level": "hard",
                    "selection_mode": "mixed",
                    "presets": [],
                }
            ],
        },
    )

    normalized = RepairKnowledgeFollowupAdapter.normalize_ask_user_question(
        ask_user,
        query="起动机启动不了怎么办",
        loaded_context=None,
    )

    group = normalized.context["field_groups"][0]
    assert normalized.question == "请先补充以下关键信息"
    assert group["label"] == "故障码情况"
    assert any(item.startswith("P") for item in group["presets"])


def test_repair_followup_generic_fault_code_presets_do_not_use_status_only_yes_code_options(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    presets = RepairKnowledgeFollowupAdapter._build_presets_without_smart_options(
        key="fault_codes",
        query="SCR 系统故障怎么排查",
        loaded_context=None,
        profile=None,
        is_power_loss=False,
        is_communication=False,
    )

    assert presets == [
        "暂未读取到具体报码",
        "当前无报码",
    ]


def test_repair_followup_normalization_sanitizes_instructional_prompt_leakage():
    leaked_text = "不要写“由于缺乏针对性的维修案例”“当前证据不足”“资料不足”等会削弱用户信任的解释"
    ask_user = AskUserQuestion(
        tool_call_id="repair_followup_prompt_leak",
        question="请补充空调不制冷的关键信息",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={
            "scene": "repair_knowledge_followup",
            "card_type": "repair_followup",
            "query": "解放空调不制冷",
            "ask_reason": leaked_text,
            "field_groups": [
                {
                    "key": "fault_phenomenon",
                    "label": leaked_text,
                    "required_level": "hard",
                    "selection_mode": "mixed",
                    "presets": [],
                    "hint": leaked_text,
                    "placeholder": leaked_text,
                }
            ],
        },
    )

    normalized = RepairKnowledgeFollowupAdapter.normalize_ask_user_question(
        ask_user,
        query="解放空调不制冷",
        loaded_context=None,
    )

    group = normalized.context["field_groups"][0]
    assert "不要写" not in normalized.context["ask_reason"]
    assert group["label"] == "当前故障现象"
    assert "不要写" not in str(group.get("hint") or "")
    assert "不要写" not in str(group.get("placeholder") or "")


def test_repair_diagnosis_query_heuristic_identifies_typical_cases():
    assert RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query("J1939 通讯故障怎么排查") is True
    assert RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query("起动机启动不了怎么办") is True
    assert RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query("高压共轨压力低是什么原因") is True
    assert RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query("传感器 5V 供电短路怎么查") is True
    assert RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query("J1939 协议是什么") is False


def test_repair_review_filters_known_start_issue_fields(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    loaded_context = build_start_issue_loaded_context()

    review = review_repair_answer_gate(
        query="冷车难起动怎么办",
        loaded_context=loaded_context,
    )

    assert review.force_ask_user is True
    assert review.ask_user is not None
    field_groups = review.ask_user.context["field_groups"]
    assert [group["key"] for group in field_groups] == ["fault_codes", "working_condition", "ecu_or_system"]
    assert all(group["key"] != "fault_phenomenon" for group in field_groups)
    assert field_groups[1]["label"] == "环境温度"


def test_repair_review_without_loaded_context_forces_ask_user_on_repair_like_query(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    review = review_repair_answer_gate(
        query="J1939 通讯故障怎么排查",
        loaded_context=None,
    )

    assert review.force_ask_user is True
    assert review.allow_ready is False
    assert review.ask_user is not None
    field_groups = review.ask_user.context["field_groups"]
    assert any(group["key"] == "fault_codes" for group in field_groups)
    assert any(group["key"] == "ecu_or_system" for group in field_groups)


def test_repair_review_electrical_sensor_query_provides_selectable_system_candidates(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    review = review_repair_answer_gate(
        query="传感器 5V 供电短路怎么查",
        loaded_context=None,
    )

    assert review.force_ask_user is True
    assert review.ask_user is not None
    group_map = {group["key"]: group for group in review.ask_user.context["field_groups"]}
    assert group_map["ecu_or_system"]["label"] == "受影响的传感器/系统"
    assert 1 <= len(group_map["ecu_or_system"]["presets"]) <= RepairKnowledgeFollowupAdapter.MAX_FIELD_OPTIONS


def test_repair_followup_adapter_prioritizes_selectable_fields_for_communication_query(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    loaded_context = {
        "loaded": True,
        "source_refs": [{"id": "repair_comm", "title": "J1939通讯故障排查", "relation": "primary"}],
        "entries": [
            {
                "title": "J1939通讯故障排查",
                "content": "J1939通讯故障需要结合具体故障现象和ECU信息才能精准排查。",
            }
        ],
        "llm_context": "J1939通讯故障需要结合具体故障现象和ECU信息才能精准排查，当前缺少这些关键限定信息以提供可执行的排查路径。",
    }

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query="J1939 通讯故障怎么排查",
        loaded_context=loaded_context,
        answer_text=loaded_context["llm_context"],
    )

    field_groups = ask_user.context["field_groups"]
    assert [group["key"] for group in field_groups] == [
        "fault_codes",
        "fault_phenomenon",
        "data_evidence",
        "ecu_or_system",
    ]
    assert field_groups[3]["label"] == "涉及的系统或控制器"
    assert field_groups[3]["presets"] == [
        "发动机控制器",
        "变速箱控制器",
        "ABS/EBS 控制器",
        "仪表/车身控制器",
        "后处理控制器",
    ]
    form_fields = ask_user.context["form"]["sections"][0]["fields"]
    assert [field["key"] for field in form_fields] == [
        "fault_codes",
        "fault_phenomenon",
        "data_evidence",
        "ecu_or_system",
    ]
    assert form_fields[3]["field_type"] == "single_select"


def test_repair_followup_service_prefers_llm_generated_observation_question(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "fake-model", raising=False)
    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_plan",
        lambda **kwargs: RepairFollowupPlanSuggestion(
            ask_reason="先区分报码方向和关键异常观测。",
            fields=[
                RepairFollowupFieldPlan(
                    key="fault_codes",
                    label="当前故障码情况",
                    selection_mode="multi",
                    options=[
                        SmartAskUserCandidate(label="P0087 燃油轨压力过低"),
                        SmartAskUserCandidate(label="P0191 燃油轨压力传感器性能异常"),
                    ],
                ),
                RepairFollowupFieldPlan(
                    key="data_evidence",
                    label="关键异常观测",
                    selection_mode="single",
                    options=[
                        SmartAskUserCandidate(label="轨压跟不上目标值"),
                        SmartAskUserCandidate(label="增压压力明显偏低"),
                        SmartAskUserCandidate(label="限扭状态已激活"),
                    ],
                ),
            ],
        ),
        raising=False,
    )

    loaded_context = {
        "loaded": True,
        "source_refs": [{"id": "repair_power", "title": "动力不足排查", "relation": "primary"}],
        "entries": [{"title": "动力不足排查", "content": "先看报码方向和关键异常观测。"}],
        "llm_context": "先看报码方向和关键异常观测。",
    }

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query="康明斯动力不足怎么办",
        loaded_context=loaded_context,
        answer_text=loaded_context["llm_context"],
    )

    field_groups = ask_user.context["field_groups"]
    assert ask_user.context["field_groups_source"] == "llm_plan"
    assert ask_user.context["ask_reason"] == "先区分报码方向和关键异常观测。"
    assert [group["label"] for group in field_groups] == ["故障码情况", "关键异常观测"]
    assert field_groups[1]["presets"] == ["轨压跟不上目标值", "增压压力明显偏低", "限扭状态已激活"]


def test_repair_review_without_loaded_context_allows_ready_when_slots_are_filled():
    review = review_repair_answer_gate(
        query="东风天龙 康明斯 ISZ13，J1939 通讯故障，整车动力受限，多个模块同时报码 U0100",
        loaded_context=None,
    )

    assert review.force_ask_user is False
    assert review.allow_ready is True
    assert review.ask_user is None

def test_repair_renderer_prompt_requires_guideline_structure_and_laoge(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    service = AgentLoopService(deps=build_test_deps(tmp_path, workbook_path))

    prompt = service._build_repair_renderer_prompt(ChatRequest(message="高压共轨压力低是什么原因"))

    assert "故障定义 -> 当前更像哪一型 -> 可能原因分类 -> 分步检查 -> 判断依据 -> 维修处理 -> 易误判点" in prompt
    assert "第一节正文第一句必须以“老哥，”开头。" in prompt
    assert "禁止再次调用 ask_user_question。" in prompt


def test_repair_renderer_prompt_for_followup_uses_user_supplemented_info(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    service = AgentLoopService(deps=build_test_deps(tmp_path, workbook_path))

    prompt = service._build_repair_renderer_prompt(
        ChatRequest(
            session_id="repair-followup",
            ask_user_answer=AskUserAnswer(
                tool_call_id="repair_followup_1",
                answer={
                    "scene": "repair_knowledge_followup",
                    "action": "submit",
                    "fields": {
                        "fault_codes": {"selected": ["防盗/启动许可相关报码"], "text": ""},
                    },
                    "summary_text": "防盗/启动许可相关报码",
                },
            ),
        )
    )

    assert "用户刚补充的信息" in prompt
    assert "故障定义 -> 当前更像哪一型 -> 可能原因分类 -> 分步检查 -> 判断依据 -> 维修处理 -> 易误判点" in prompt
    assert "第一节正文第一句必须以“老哥，”开头。" in prompt


def test_repair_renderer_fallback_content_matches_mechanic_guideline_structure(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    service = AgentLoopService(deps=build_test_deps(tmp_path, workbook_path))

    content = service._build_repair_renderer_fallback_content(
        query="起动机启动不了怎么办",
        summary_text="起动机能转但发动机不着车；冷车明显；防盗/启动许可相关报码",
        field_values={
            "fault_phenomenon": {"selected": ["起动机能转但发动机不着车"], "text": ""},
            "working_condition": {"selected": ["冷车明显"], "text": ""},
            "fault_codes": {"selected": ["防盗/启动许可相关报码"], "text": ""},
        },
        loaded_context=build_starter_issue_loaded_context(),
    )

    assert content.startswith("### 当前判断\n老哥，")
    assert "### 检查前准备" in content
    assert "### 分步检查" in content
    assert "### 异常后怎么走" in content
    assert "### 处理动作" in content
    assert "### 复验" in content
    assert "### 易误判点" in content
    assert "由于缺乏针对性的维修案例" not in content
    assert "为了更精准地协助您" not in content
    assert "请补充" not in content


def test_agent_loop_process_uses_repair_gate_for_repair_like_query_without_title_match(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "ask_user_question",
                    {
                        "question": "请先补充以下关键信息",
                        "input_type": "text",
                        "allow_free_input": True,
                        "options": [],
                        "context": {
                            "scene": "repair_knowledge_followup",
                            "card_type": "repair_followup",
                            "field_groups": [
                                {
                                    "key": "fault_codes",
                                    "label": "故障码情况",
                                    "required_level": "hard",
                                    "selection_mode": "single",
                                    "presets": [],
                                }
                            ],
                        },
                    },
                    tool_call_id="repair_gate_ask_heuristic",
                )
            ]
        )

    def main_llm(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        raise AssertionError("main agent should not run when repair gate already asked user")

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(main_llm),
        gate_model_override=FunctionModel(gate_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="J1939 通讯故障怎么排查")))

    assert response.type == "ask_user"
    assert response.ask_user is not None
    assert response.ask_user.context["scene"] == "repair_knowledge_followup"
    assert response.ask_user.context["field_groups"][0]["key"] == "fault_codes"
    assert response.ask_user.context["field_groups"][0]["presets"] == [
        "U0100 与发动机控制模块通讯丢失",
        "U0101 与变速箱控制模块通讯丢失",
        "U0121 与ABS/EBS模块通讯丢失",
        "U0140 与车身/仪表控制模块通讯丢失",
        "U0073 控制模块通信总线关闭",
    ]


def test_repair_gate_forces_renderer_when_review_allows_ready(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)
    session_id = "repair-gate-force-ready"
    tool_call_id = "repair_gate_force_ready_1"

    def gate_llm(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="当前信息已够，可以直接回答。")])

    def renderer_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        prompt_text = extract_request_text(messages)
        if "请只输出结构化 RepairRenderPlan。" in prompt_text or not prompt_text.strip():
            return ModelResponse(parts=[TextPart(content="ignored")])
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "### 当前判断\n"
                        "老哥，先把主线放在总线物理层和 ECU 公共供电，不先盯着单个模块。\n\n"
                        "### 检查前准备\n"
                        "确认当前报码和现象可以稳定复现，再开始量主干网络。\n\n"
                        "### 分步检查\n"
                        "1. 先断电量主干电阻，正常应接近 60 欧。\n"
                        "2. 再通电看 CAN_H/CAN_L 电压和多个模块在线情况。\n"
                        "3. 同时核对 DCU 和相关模块供电、搭铁。\n"
                        "4. 基础条件正常后，再做分段隔离。\n\n"
                        "### 异常后怎么走\n"
                        "1. 如果电阻异常，先修终端、电缆或支路短路。\n"
                        "2. 如果电阻正常但电压被拉偏，优先找拖垮总线的节点。\n\n"
                        "### 处理动作\n"
                        "先修网络本体和公共供电，再决定是否处理单个控制器。\n\n"
                        "### 复验\n"
                        "修复后复测电阻、电压和在线状态，确认报码不再当前出现。\n\n"
                        "### 易误判点\n"
                        "没量电阻和电压前不要先换模块。"
                    )
                )
            ]
        )

    def main_llm(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        raise AssertionError("main agent should not run when review already forced renderer")

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(main_llm),
        gate_model_override=FunctionModel(gate_llm),
        renderer_model_override=FunctionModel(renderer_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)
    ask_user = AskUserQuestion(
        tool_call_id=tool_call_id,
        question="请先补充以下关键信息",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={
            "scene": "repair_knowledge_followup",
            "card_type": "repair_followup",
            "query": "J1939 通讯故障怎么排查",
            "repair_knowledge_query": "J1939 通讯故障怎么排查",
        },
    )
    deps.deferred_state_store.save(
        session_id=session_id,
        state=DeferredState(
            tool_call_id=tool_call_id,
            tool_name="ask_user_question",
            message_history_json=service._serialize_history(
                service._build_synthetic_ask_user_history(
                    full_messages=[ModelRequest.user_text_prompt("J1939 通讯故障怎么排查")],
                    ask_user=ask_user,
                )
            ),
            payload=ask_user.model_dump(mode="json"),
        ),
    )

    response = asyncio.run(
        service.process(
            ChatRequest(
                session_id=session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=tool_call_id,
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "ecu_or_system": {"selected": [], "text": "东风天龙 + 康明斯 ISZ13"},
                            "fault_phenomenon": {"selected": ["整车动力受限/限速/限扭"], "text": ""},
                            "fault_codes": {"selected": ["多个模块同时报码"], "text": "U0100"},
                        },
                        "summary_text": "东风天龙 + 康明斯 ISZ13；整车动力受限/限速/限扭；多个模块同时报码，U0100",
                    },
                ),
            )
        )
    )

    assert response.type == "message"
    assert response.content.startswith("### 当前判断\n老哥，")


def test_agent_loop_service_resumes_synthetic_repair_followup(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def repair_knowledge_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_knowledge_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_knowledge_2",
                    )
                ]
            )
        if tool_return.tool_name == "ask_user_question":
            assert tool_return.content["answer"]["scene"] == "repair_knowledge_followup"
            assert tool_return.content["answer"]["action"] == "submit"
            return ModelResponse(parts=[TextPart(content="### 维修建议\n先核对报码，再结合轨压跟随和限扭状态继续判断。")])

        assert tool_return.tool_name == "get_repair_knowledge_context"
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "### 还需补充\n"
                        "请先补充故障码、动力相关数据流和 ECU 版本信息。\n\n"
                        "[上传数据流 CSV 文件进行诊断]"
                    )
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(repair_knowledge_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)
    service._repair_gate_agent = None
    service._repair_renderer_agent = None

    first = asyncio.run(service.process(ChatRequest(message="康明斯动力不足怎么办")))

    assert first.type == "ask_user"
    assert first.ask_user is not None

    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "fault_codes": {"selected": ["有明确故障码"], "text": "P0087"},
                            "data_evidence": {"selected": ["轨压跟随", "限扭状态"], "text": ""},
                        },
                        "quick_action": None,
                        "summary_text": "故障码 P0087，已掌握轨压跟随和限扭状态",
                    },
                ),
            )
        )
    )

    assert second.type == "message"
    assert isinstance(second.content, str)
    assert second.content == "### 维修建议\n先核对报码，再结合轨压跟随和限扭状态继续判断。"
    assert second.metadata["repair_knowledge_primary_title"] == "康明斯动力不足故障诊断分析提示词"
    assert "repair_renderer_fallback" not in second.metadata
    assert "repair_followup_rewritten" not in second.metadata


def test_repair_gate_sanitizes_malformed_repair_followup_payload(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_gate_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_gate_2",
                    )
                ]
            )

        assert tool_return.tool_name == "get_repair_knowledge_context"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "ask_user_question",
                    {
                        "question": "请先补充以下信息",
                        "input_type": "text",
                        "allow_free_input": True,
                        "options": [
                            {"key": "brand", "label": "车辆品牌及发动机型号"},
                            {"key": "code_status", "label": "是否亮故障灯或有故障码？"},
                            {"key": "assist", "label": "我已经读取到故障码，请协助分析"},
                        ],
                        "context": {
                            "scene": "repair_knowledge_followup",
                            "card_type": "repair_followup",
                            "field_groups": [
                                {
                                    "key": "fault_phenomenon",
                                    "label": "车辆信息与故障现象",
                                    "required_level": "hard",
                                    "selection_mode": "mixed",
                                    "options": [
                                        "车辆品牌及发动机型号",
                                        "是否亮故障灯或有故障码？",
                                        "环境温度大概是多少？",
                                        "是完全打不着火，还是打着后熄火/启动时间过长？",
                                    ],
                                }
                            ],
                        },
                    },
                    tool_call_id="repair_gate_bad_ask",
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        gate_model_override=FunctionModel(gate_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    events = asyncio.run(collect_stream_events(service, ChatRequest(message="康明斯动力不足怎么办")))
    done_event = next(event for event in events if event.type == AgentEventType.DONE)
    response = done_event.metadata["response"]

    assert response["type"] == "ask_user"
    assert response["ask_user"] is not None
    field_groups = response["ask_user"]["context"]["field_groups"]
    assert any(group["key"] == "fault_codes" for group in field_groups)
    assert any(group["key"] == "data_evidence" for group in field_groups)
    assert any(group["key"] == "ecu_or_system" for group in field_groups)

    flattened_presets = [preset for group in field_groups for preset in group.get("presets", [])]
    assert "车辆品牌及发动机型号" not in flattened_presets
    assert "是否亮故障灯或有故障码？" not in flattened_presets
    assert "环境温度大概是多少？" not in flattened_presets
    assert "是完全打不着火，还是打着后熄火/启动时间过长？" not in flattened_presets

    quick_actions = response["ask_user"]["context"]["quick_actions"]
    assert [item["label"] for item in quick_actions] == ["我已经读取到故障码，请协助分析"]
    assert [item["label"] for item in response["ask_user"]["options"]] == ["我已经读取到故障码，请协助分析"]


def test_agent_loop_service_normalizes_repair_meta_reasoning_words(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def repair_knowledge_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_knowledge_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_knowledge_2",
                    )
                ]
            )

        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "### 初步判断\n"
                        "根据维修经验，诊断的核心逻辑是区分“系统限扭”与“性能不足”。\n\n"
                        "### 维修建议\n"
                        "优先检查轨压跟随、进气压力和限扭状态。"
                    )
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(repair_knowledge_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)
    service._repair_gate_agent = None
    service._repair_renderer_agent = None

    response = asyncio.run(service.process(ChatRequest(message="康明斯动力不足怎么办")))

    assert response.type == "message"
    assert isinstance(response.content, str)
    assert "根据维修经验" not in response.content
    assert "诊断的核心逻辑" not in response.content
    assert "先区分“系统限扭”与“性能不足”。" in response.content


def test_repair_followup_message_no_longer_rewrites_to_fallback_guideline(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)
    service = AgentLoopService(deps=deps)
    session_id = "repair-followup-rewrite"
    tool_call_id = "repair_followup_rewrite_1"

    deps.deferred_state_store.save(
        session_id=session_id,
        state=DeferredState(
            tool_call_id=tool_call_id,
            tool_name="ask_user_question",
            message_history_json="[]",
            payload={
                "ask_user": {
                    "tool_call_id": tool_call_id,
                    "question": "请先补充以下关键信息",
                    "input_type": "text",
                    "allow_free_input": True,
                    "context": {
                        "scene": "repair_knowledge_followup",
                        "card_type": "repair_followup",
                        "query": "J1939 通讯故障怎么排查",
                        "repair_knowledge_query": "J1939 通讯故障怎么排查",
                    },
                }
            },
        ),
    )

    content, metadata = service._maybe_rewrite_repair_followup_message(
        request=ChatRequest(
            session_id=session_id,
            ask_user_answer=AskUserAnswer(
                tool_call_id=tool_call_id,
                answer={
                    "scene": "repair_knowledge_followup",
                    "action": "submit",
                    "fields": {
                        "ecu_or_system": {"selected": [], "text": "东风天龙 + 康明斯 ISZ13"},
                        "fault_phenomenon": {"selected": ["整车动力受限/限速/限扭"], "text": ""},
                        "fault_codes": {"selected": ["多个模块同时报码"], "text": "U0100"},
                    },
                    "summary_text": "东风天龙 + 康明斯 ISZ13；整车动力受限/限速/限扭；多个模块同时报码，U0100",
                },
            ),
        ),
        active_deps=deps,
        session_id=session_id,
        full_messages=[],
        content=(
            "### 初步判断\n"
            "U0100 通常表示与发动机控制模块通讯丢失。\n\n"
            "### 优先检查\n"
            "先量终端电阻和总线电压。"
        ),
        extra_metadata={},
    )

    assert isinstance(content, str)
    assert content.startswith("### 初步判断")
    assert metadata == {}


def test_repair_gate_keeps_asking_when_fault_code_only_has_status_without_specific_code(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "SCR 系统故障"},
                        tool_call_id="repair_gate_scr_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_gate_scr_2",
                    )
                ]
            )

        assert tool_return.tool_name == "get_repair_knowledge_context"
        return ModelResponse(parts=[TextPart(content="__READY_TO_ANSWER__")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        gate_model_override=FunctionModel(gate_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)
    session_id = "repair-followup-faultcode-status-only"
    tool_call_id = "repair_followup_faultcode_status_only_1"
    ask_user = AskUserQuestion(
        tool_call_id=tool_call_id,
        question="请先补充以下关键信息",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={
            "scene": "repair_knowledge_followup",
            "card_type": "repair_followup",
            "query": "SCR 系统故障",
            "repair_knowledge_query": "SCR 系统故障",
        },
    )
    deps.deferred_state_store.save(
        session_id=session_id,
        state=DeferredState(
            tool_call_id=tool_call_id,
            tool_name="ask_user_question",
            message_history_json=service._serialize_history(
                service._build_synthetic_ask_user_history(
                    full_messages=[ModelRequest.user_text_prompt("SCR 系统故障")],
                    ask_user=ask_user,
                )
            ),
            payload={"ask_user": ask_user.model_dump(mode="json")},
        ),
    )

    response = asyncio.run(
        service.process(
            ChatRequest(
                session_id=session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=tool_call_id,
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "fault_codes": {"selected": ["有明确故障码"], "text": ""},
                        },
                        "summary_text": "有明确故障码",
                    },
                ),
            )
        )
    )

    assert response.type == "ask_user"
    assert response.ask_user is not None
    fault_group = next(group for group in response.ask_user.context["field_groups"] if group["key"] == "fault_codes")
    assert any(item.startswith("P") for item in fault_group["presets"])


def test_repair_followup_resume_prompt_includes_answered_fields_without_message(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)
    service = AgentLoopService(deps=deps)
    session_id = "repair-followup-resume-prompt"
    request = ChatRequest(message="SCR 系统故障", session_id=session_id)
    active_deps = service._prepare_request_runtime_deps(runtime_deps=deps, request=request, session_id=session_id)

    answer = build_repair_followup_answer(
        "repair_followup_1",
        fields={
            "fault_codes": {"selected": ["P0101 空气流量/进气量信号异常"], "text": ""},
            "fault_phenomenon": {"selected": ["排气管外壁/喷嘴处有结晶"], "text": ""},
            "working_condition": {"selected": ["行驶中无法建立压力喷射"], "text": ""},
        },
        summary_text="P0101 空气流量/进气量信号异常；排气管外壁/喷嘴处有结晶；行驶中无法建立压力喷射",
    )
    service._record_case_context_user_answer(active_deps=active_deps, answer=answer)

    prompt = service._build_user_prompt_with_case_context(
        active_deps=active_deps,
        request=ChatRequest(message="SCR 系统故障", session_id=session_id, ask_user_answer=answer),
        message_history=None,
    )

    assert prompt is not None
    assert "[REPAIR_FOLLOWUP_RESUME]" in prompt
    assert "原始问题：SCR 系统故障" in prompt
    assert "禁止再次用 ask_user_question 重复询问这些已经回答过的字段" in prompt
    assert "请基于已加载资料、共享上下文和用户刚补充的信息继续判断当前是否还需要追问。" in prompt
    assert "fault_codes" in prompt
    assert "P0101 空气流量/进气量信号异常" in prompt
    assert "排气管外壁/喷嘴处有结晶" in prompt
    assert "行驶中无法建立压力喷射" in prompt


def test_repair_followup_resume_prompt_merges_previous_round_answers(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)
    service = AgentLoopService(deps=deps)
    session_id = "repair-followup-resume-merge"
    request = ChatRequest(message="J1939 通讯故障怎么排查", session_id=session_id)
    active_deps = service._prepare_request_runtime_deps(runtime_deps=deps, request=request, session_id=session_id)

    first_answer = build_repair_followup_answer(
        "repair_followup_hist_1",
        fields={
            "ecu_or_system": {"selected": ["东风天龙"], "text": ""},
            "fault_codes": {"selected": ["当前无报码"], "text": ""},
            "data_evidence": {"selected": ["J1939 主干电阻"], "text": ""},
        },
        summary_text="东风天龙；当前无报码；J1939 主干电阻",
    )
    service._record_case_context_user_answer(active_deps=active_deps, answer=first_answer)

    second_answer = build_repair_followup_answer(
        "repair_followup_hist_2",
        fields={
            "working_condition": {"selected": ["偶发"], "text": ""},
        },
        summary_text="偶发",
    )
    prompt = service._build_user_prompt_with_case_context(
        active_deps=active_deps,
        request=ChatRequest(session_id=session_id, ask_user_answer=second_answer),
        message_history=None,
    )

    assert prompt is not None
    assert "ecu_or_system" in prompt
    assert "fault_codes" in prompt
    assert "data_evidence" in prompt
    assert "working_condition" in prompt
    assert "东风天龙" in prompt
    assert "当前无报码" in prompt
    assert "J1939 主干电阻" in prompt
    assert "偶发" in prompt


def test_repair_followup_review_ask_user_keeps_original_query(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="当前信息已够，可以直接回答。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        gate_model_override=FunctionModel(gate_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)
    session_id = "repair-followup-original-query"
    tool_call_id = "repair_followup_original_query_1"
    ask_user = AskUserQuestion(
        tool_call_id=tool_call_id,
        question="请先补充以下关键信息",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={
            "scene": "repair_knowledge_followup",
            "card_type": "repair_followup",
            "query": "J1939 通讯故障怎么排查",
            "repair_knowledge_query": "J1939 通讯故障怎么排查",
        },
    )
    deps.deferred_state_store.save(
        session_id=session_id,
        state=DeferredState(
            tool_call_id=tool_call_id,
            tool_name="ask_user_question",
            message_history_json=service._serialize_history(
                service._build_synthetic_ask_user_history(
                    full_messages=[ModelRequest.user_text_prompt("J1939 通讯故障怎么排查")],
                    ask_user=ask_user,
                )
            ),
            payload={"ask_user": ask_user.model_dump(mode="json")},
        ),
    )

    response = asyncio.run(
        service.process(
            ChatRequest(
                session_id=session_id,
                ask_user_answer=build_repair_followup_answer(
                    tool_call_id,
                    fields={
                        "fault_codes": {"selected": ["U0100"], "text": ""},
                    },
                    summary_text="U0100",
                ),
            )
        )
    )

    assert response.type == "ask_user"
    assert response.ask_user is not None
    assert response.ask_user.context["query"] == "J1939 通讯故障怎么排查"
    assert response.ask_user.context["repair_knowledge_query"] == "J1939 通讯故障怎么排查"


def test_repair_guideline_answer_moves_laoge_to_first_section():
    adjusted = AgentLoopService._ensure_repair_guideline_salutation(
        "### 故障定义\nU0100 指向发动机控制模块通讯丢失。\n\n"
        "### 当前更像哪一型\n老哥，这更像总线物理层故障。"
    )

    assert adjusted.startswith("### 故障定义\n老哥，U0100 指向发动机控制模块通讯丢失。")


def test_repair_followup_multi_round_gate_keeps_previous_round_fields(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": "请先补充以下关键信息",
                            "input_type": "text",
                            "allow_free_input": True,
                            "options": [],
                            "context": {
                                "scene": "repair_knowledge_followup",
                                "card_type": "repair_followup",
                                "query": "康明斯动力不足怎么办",
                                "repair_knowledge_query": "康明斯动力不足怎么办",
                                "field_groups": [
                                    {"key": "working_condition", "label": "出现工况", "required_level": "strong"},
                                    {"key": "data_evidence", "label": "已掌握的关键数据", "required_level": "hard"},
                                ],
                            },
                        },
                        tool_call_id="repair_gate_power_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        assert tool_return.tool_name == "ask_user_question"
        answer = tool_return.content["answer"]
        if answer.get("summary_text") == "急加速明显；轨压跟随":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": "再补一下报码情况",
                            "input_type": "text",
                            "allow_free_input": True,
                            "options": [],
                            "context": {
                                "scene": "repair_knowledge_followup",
                                "card_type": "repair_followup",
                                "query": "康明斯动力不足怎么办",
                                "repair_knowledge_query": "康明斯动力不足怎么办",
                                "field_groups": [
                                    {"key": "fault_codes", "label": "故障码情况", "required_level": "hard"},
                                ],
                            },
                        },
                        tool_call_id="repair_gate_power_2",
                    )
                ]
            )

        return ModelResponse(parts=[TextPart(content="先看报码和关键数据流。")])

    def renderer_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        prompt_text = extract_request_text(messages)
        if "请只输出结构化 RepairRenderPlan。" in prompt_text or not prompt_text.strip():
            return ModelResponse(parts=[TextPart(content="ignored")])
        assert "working_condition:急加速明显" in prompt_text
        assert "data_evidence:轨压跟随" in prompt_text
        assert "fault_codes:当前无报码" in prompt_text
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "### 当前判断\n"
                        "老哥，急加速明显且已掌握轨压跟随、当前无报码时，先把重点放在关键数据流和基础供油条件。\n\n"
                        "### 检查前准备\n"
                        "先确认急加速工况可以稳定复现，同时把轨压跟随数据准备好。\n\n"
                        "### 分步检查\n"
                        "1. 先看急加速瞬间轨压跟随是否明显掉队。\n"
                        "2. 如果轨压跟随异常，再继续核对低压供油和限扭条件。\n"
                        "3. 如果轨压跟随基本正常，再回头看进气和负载侧。\n\n"
                        "### 异常后怎么走\n"
                        "1. 如果轨压掉队，先查供油侧。\n"
                        "2. 如果轨压正常，转到进气和限扭方向。\n\n"
                        "### 处理动作\n"
                        "按数据流结果分别处理供油或进气限扭问题。\n\n"
                        "### 复验\n"
                        "处理后再跑一次急加速，确认轨压跟随恢复。\n\n"
                        "### 易误判点\n"
                        "没有报码不代表轨压方向可以直接排除。"
                    )
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(gate_llm),
        gate_model_override=FunctionModel(gate_llm),
        renderer_model_override=FunctionModel(renderer_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="康明斯动力不足怎么办")))
    assert first.type == "ask_user"
    assert first.ask_user is not None

    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "working_condition": {"selected": ["急加速明显"], "text": ""},
                            "data_evidence": {"selected": ["轨压跟随"], "text": ""},
                        },
                        "summary_text": "急加速明显；轨压跟随",
                    },
                ),
            )
        )
    )
    assert second.type == "ask_user"
    assert second.ask_user is not None

    third = asyncio.run(
        service.process(
            ChatRequest(
                session_id=second.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=second.ask_user.tool_call_id,
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "fault_codes": {"selected": ["当前无报码"], "text": ""},
                        },
                        "summary_text": "当前无报码",
                    },
                ),
            )
        )
    )

    assert third.type == "message"
    assert third.metadata["repair_render_frame"] == "symptom_diagnosis"
    assert "急加速明显" in third.content
    assert "轨压跟随" in third.content
    assert "当前无报码" in third.content


def test_repair_answer_normalizer_strips_trust_eroding_phrases_and_textual_followup():
    normalized = AgentLoopService._normalize_repair_knowledge_answer_content(
        content=(
            "由于缺乏针对性的维修案例，建议您按照以下标准步骤进行物理排查。\n\n"
            "### 维修建议\n"
            "先检查 J1939 主干线电阻、终端电阻和 CAN_H/CAN_L 对地短路情况。\n\n"
            "如果问题仍然无法解决，为了提供更精确的排查路径，请补充您的车辆信息：\n"
            "车辆品牌及发动机型号\n"
            "是否亮故障灯或有故障码\n"
            "当前最明显的故障现象"
        ),
        metadata={"repair_knowledge_sources": [{"id": "repair_knowledge_2"}]},
    )

    assert isinstance(normalized, str)
    assert "由于缺乏针对性的维修案例" not in normalized
    assert "为了提供更精确的排查路径" not in normalized
    assert "请补充您的车辆信息" not in normalized
    assert "车辆品牌及发动机型号" not in normalized
    assert normalized.startswith("### 维修建议")
    assert "先检查 J1939 主干线电阻" in normalized


def test_repair_gate_stream_returns_ask_user_without_text_deltas(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_gate_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_gate_2",
                    )
                ]
            )

        assert tool_return.tool_name == "get_repair_knowledge_context"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "ask_user_question",
                    {
                        "question": "请先补充以下关键信息",
                        "input_type": "text",
                        "options": [],
                        "allow_free_input": True,
                        "input_hint": "优先点选，若没有合适选项再手动补充",
                        "context": {
                            "scene": "repair_knowledge_followup",
                            "card_type": "repair_followup",
                            "field_groups": [
                                {
                                    "key": "fault_codes",
                                    "label": "当前故障码情况",
                                    "required_level": "hard",
                                    "selection_mode": "mixed",
                                    "presets": ["有明确故障码", "暂无故障码"],
                                }
                            ],
                        },
                    },
                    tool_call_id="repair_gate_ask_1",
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        gate_model_override=FunctionModel(gate_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    events = asyncio.run(collect_stream_events(service, ChatRequest(message="康明斯动力不足怎么办")))

    assert any(event.type == AgentEventType.START for event in events)
    assert any(event.type == AgentEventType.HINT for event in events)
    assert [event.content for event in events if event.type == AgentEventType.TEXT_DELTA] == []
    done_event = next(event for event in events if event.type == AgentEventType.DONE)
    assert done_event.metadata["response"]["type"] == "ask_user"
    assert done_event.metadata["response"]["ask_user"]["context"]["scene"] == "repair_knowledge_followup"
    assert done_event.metadata["response"]["ask_user"]["context"]["field_groups"][0]["presets"] == ["暂未读取到具体报码", "当前无报码"]


def test_repair_gate_stream_overrides_ready_to_ask_user_when_required_info_missing(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_gate_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_gate_2",
                    )
                ]
            )

        assert tool_return.tool_name == "get_repair_knowledge_context"
        return ModelResponse(parts=[TextPart(content="__READY_TO_ANSWER__")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        gate_model_override=FunctionModel(gate_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    events = asyncio.run(collect_stream_events(service, ChatRequest(message="康明斯动力不足怎么办")))

    assert [event.content for event in events if event.type == AgentEventType.TEXT_DELTA] == []
    done_event = next(event for event in events if event.type == AgentEventType.DONE)
    assert done_event.metadata["response"]["type"] == "ask_user"
    assert done_event.metadata["response"]["ask_user"]["context"]["scene"] == "repair_knowledge_followup"
    field_groups = done_event.metadata["response"]["ask_user"]["context"]["field_groups"]
    assert any(group["key"] == "fault_codes" for group in field_groups)
    assert any(group["key"] == "data_evidence" for group in field_groups)


def test_repair_gate_stream_keeps_text_deltas_after_ready_check(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)
    query = "康明斯 P0087，EDC17，急加速时动力不足，已有轨压跟随和限扭状态数据，怎么办"

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": query},
                        tool_call_id="repair_gate_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_gate_2",
                    )
                ]
            )

        assert tool_return.tool_name == "get_repair_knowledge_context"
        return ModelResponse(parts=[TextPart(content="__READY_TO_ANSWER__")])

    async def renderer_stream(messages: list[ModelMessage], _: AgentInfo):
        prompt_text = extract_request_text(messages)
        assert "直接回答用户当前问题" in prompt_text
        yield "### 维修建议\n"
        yield "先检查轨压跟随和限扭状态。"

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        gate_model_override=FunctionModel(gate_llm),
        renderer_model_override=FunctionModel(stream_function=renderer_stream),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    events = asyncio.run(collect_stream_events(service, ChatRequest(message=query)))

    chunks = [event.content for event in events if event.type == AgentEventType.TEXT_DELTA]
    assert chunks == ["### 维修建议\n先检查轨压跟随和限扭状态。"]
    done_event = next(event for event in events if event.type == AgentEventType.DONE)
    assert done_event.metadata["response"]["type"] == "message"
    assert done_event.metadata["full_content"] == "### 维修建议\n先检查轨压跟随和限扭状态。"


def test_repair_followup_resume_falls_back_when_model_unavailable(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def repair_knowledge_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "起动机启动不了怎么办"},
                        tool_call_id="repair_gate_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_gate_2",
                    )
                ]
            )
        if tool_return.tool_name == "ask_user_question":
            raise RuntimeError(
                "Set the OPENROUTER_API_KEY environment variable or pass it via OpenRouterProvider(api_key=...) to use the OpenRouter provider."
            )

        assert tool_return.tool_name == "get_repair_knowledge_context"
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "### 还需补充\n"
                        "请先补充故障现象、出现条件和故障码情况。\n\n"
                        "[我已经补充完关键信息]"
                    )
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(repair_knowledge_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="起动机启动不了怎么办")))

    assert first.type == "ask_user"
    assert first.ask_user is not None

    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "fault_phenomenon": {"selected": ["起动机能转但发动机不着车"], "text": ""},
                            "working_condition": {"selected": ["冷车明显"], "text": ""},
                            "fault_codes": {"selected": ["防盗/启动许可相关报码"], "text": ""},
                        },
                        "summary_text": "起动机能转但发动机不着车；冷车明显；防盗/启动许可相关报码",
                    },
                ),
            )
        )
    )

    assert second.type == "error"
    assert second.content["message"] == "系统处理请求时发生错误，请稍后重试。"
    assert second.content["error_code"] == "AGENT_RUNTIME_ERROR"
    assert "OPENROUTER_API_KEY" in second.content["reason"]


def test_repair_followup_stream_resume_falls_back_when_model_unavailable(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_repair_knowledge_titles",
                        {"query": "康明斯动力不足怎么办"},
                        tool_call_id="repair_gate_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "lookup_repair_knowledge_titles":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_repair_knowledge_context",
                        {"entry_ids": ["repair_knowledge_2"]},
                        tool_call_id="repair_gate_2",
                    )
                ]
            )
        if tool_return.tool_name == "ask_user_question":
            return ModelResponse(parts=[TextPart(content="__READY_TO_ANSWER__")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "ask_user_question",
                    {
                        "question": "请先补充以下关键信息",
                        "input_type": "text",
                        "options": [],
                        "allow_free_input": True,
                        "context": {
                            "scene": "repair_knowledge_followup",
                            "card_type": "repair_followup",
                            "field_groups": [
                                {"key": "fault_phenomenon", "label": "当前故障现象", "required_level": "strong"},
                                {"key": "working_condition", "label": "出现问题的工况", "required_level": "hard"},
                                {"key": "fault_codes", "label": "当前故障码情况", "required_level": "hard"},
                            ],
                        },
                    },
                    tool_call_id="repair_gate_ask_1",
                )
            ]
        )

    def broken_main(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        raise RuntimeError(
            "Set the OPENROUTER_API_KEY environment variable or pass it via OpenRouterProvider(api_key=...) to use the OpenRouter provider."
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(broken_main),
        gate_model_override=FunctionModel(gate_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first_events = asyncio.run(collect_stream_events(service, ChatRequest(message="康明斯动力不足怎么办")))
    first_done = next(event for event in first_events if event.type == AgentEventType.DONE)
    first_response = first_done.metadata["response"]

    assert first_response["type"] == "ask_user"

    second_events = asyncio.run(
        collect_stream_events(
            service,
            ChatRequest(
                session_id=first_response["session_id"],
                    ask_user_answer=AskUserAnswer(
                        tool_call_id=first_response["ask_user"]["tool_call_id"],
                        answer={
                            "scene": "repair_knowledge_followup",
                            "action": "submit",
                            "fields": {
                                "fault_phenomenon": {"selected": ["加速无力"], "text": ""},
                                "working_condition": {"selected": ["急加速明显"], "text": ""},
                                "fault_codes": {"selected": ["有明确故障码"], "text": "P0087"},
                                "data_evidence": {"selected": ["轨压跟随", "限扭状态"], "text": ""},
                            },
                            "summary_text": "加速无力；急加速明显；故障码 P0087；已掌握轨压跟随和限扭状态",
                        },
                    ),
                ),
            )
    )

    error_event = next(event for event in second_events if event.type == AgentEventType.ERROR)
    assert error_event.message == "系统处理请求时发生错误，请稍后重试。"
    assert "OPENROUTER_API_KEY" in error_event.metadata["detail"]


def test_repair_followup_resume_without_loaded_context_falls_back_when_model_unavailable(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": "请先补充以下关键信息",
                            "input_type": "text",
                            "allow_free_input": True,
                            "options": [],
                            "context": {
                                "scene": "repair_knowledge_followup",
                                "card_type": "repair_followup",
                                "query": "J1939 通讯故障怎么排查",
                                "repair_knowledge_query": "J1939 通讯故障怎么排查",
                                "field_groups": [
                                    {
                                        "key": "ecu_or_system",
                                        "label": "涉及的单元/系统",
                                        "required_level": "strong",
                                        "selection_mode": "mixed",
                                        "presets": ["东风天龙"],
                                    },
                                    {
                                        "key": "fault_codes",
                                        "label": "故障码情况",
                                        "required_level": "hard",
                                        "selection_mode": "mixed",
                                        "presets": ["当前无报码", "报码偶发"],
                                    },
                                    {
                                        "key": "data_evidence",
                                        "label": "已掌握的关键数据",
                                        "required_level": "hard",
                                        "selection_mode": "mixed",
                                        "presets": ["J1939 主干电阻"],
                                    },
                                ],
                            },
                        },
                        tool_call_id="repair_gate_j1939_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        assert tool_return.tool_name == "ask_user_question"
        answer = tool_return.content["answer"]
        if answer.get("summary_text") == "东风天龙；当前无报码；J1939 主干电阻":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": "请继续补充",
                            "input_type": "text",
                            "allow_free_input": True,
                            "options": [],
                            "context": {
                                "scene": "repair_knowledge_followup",
                                "card_type": "repair_followup",
                                "query": "J1939 通讯故障怎么排查",
                                "repair_knowledge_query": "J1939 通讯故障怎么排查",
                                "field_groups": [
                                    {
                                        "key": "fault_codes",
                                        "label": "故障码情况",
                                        "required_level": "hard",
                                        "selection_mode": "mixed",
                                        "presets": ["报码偶发", "当前无报码"],
                                    }
                                ],
                            },
                        },
                        tool_call_id="repair_gate_j1939_2",
                    )
                ]
            )

        return ModelResponse(parts=[TextPart(content="__READY_TO_ANSWER__")])

    def broken_main(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        raise RuntimeError("boom-main")

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(broken_main),
        gate_model_override=FunctionModel(gate_llm),
        renderer_model_override=FunctionModel(broken_main),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="J1939 通讯故障怎么排查")))
    assert first.type == "ask_user"
    assert first.ask_user is not None

    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "ecu_or_system": {"selected": ["东风天龙"], "text": ""},
                            "fault_codes": {"selected": ["当前无报码"], "text": ""},
                            "data_evidence": {"selected": ["J1939 主干电阻"], "text": ""},
                        },
                        "summary_text": "东风天龙；当前无报码；J1939 主干电阻",
                    },
                ),
            )
        )
    )
    assert second.type == "ask_user"
    assert second.ask_user is not None

    third = asyncio.run(
        service.process(
            ChatRequest(
                session_id=second.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=second.ask_user.tool_call_id,
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "fault_codes": {"selected": ["报码偶发"], "text": ""},
                        },
                        "summary_text": "报码偶发",
                    },
                ),
            )
        )
    )

    assert third.type == "error"
    assert third.content["message"] == "系统处理请求时发生错误，请稍后重试。"
    assert third.content["error_code"] == "AGENT_RUNTIME_ERROR"
    assert third.content["reason"] == "boom-main"


def test_repair_followup_stream_resume_without_loaded_context_falls_back_when_model_unavailable(tmp_path):
    workbook_path = tmp_path / "repair.xlsx"
    build_repair_workbook(workbook_path)
    deps = build_test_deps(tmp_path, workbook_path)

    def gate_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": "请先补充以下关键信息",
                            "input_type": "text",
                            "allow_free_input": True,
                            "options": [],
                            "context": {
                                "scene": "repair_knowledge_followup",
                                "card_type": "repair_followup",
                                "query": "J1939 通讯故障怎么排查",
                                "repair_knowledge_query": "J1939 通讯故障怎么排查",
                                "field_groups": [
                                    {
                                        "key": "ecu_or_system",
                                        "label": "涉及的单元/系统",
                                        "required_level": "strong",
                                        "selection_mode": "mixed",
                                        "presets": ["东风天龙"],
                                    },
                                    {
                                        "key": "fault_codes",
                                        "label": "故障码情况",
                                        "required_level": "hard",
                                        "selection_mode": "mixed",
                                        "presets": ["当前无报码", "报码偶发"],
                                    },
                                    {
                                        "key": "data_evidence",
                                        "label": "已掌握的关键数据",
                                        "required_level": "hard",
                                        "selection_mode": "mixed",
                                        "presets": ["J1939 主干电阻"],
                                    },
                                ],
                            },
                        },
                        tool_call_id="repair_gate_stream_j1939_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        assert tool_return.tool_name == "ask_user_question"
        answer = tool_return.content["answer"]
        if answer.get("summary_text") == "东风天龙；当前无报码；J1939 主干电阻":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": "请继续补充",
                            "input_type": "text",
                            "allow_free_input": True,
                            "options": [],
                            "context": {
                                "scene": "repair_knowledge_followup",
                                "card_type": "repair_followup",
                                "query": "J1939 通讯故障怎么排查",
                                "repair_knowledge_query": "J1939 通讯故障怎么排查",
                                "field_groups": [
                                    {
                                        "key": "fault_codes",
                                        "label": "故障码情况",
                                        "required_level": "hard",
                                        "selection_mode": "mixed",
                                        "presets": ["报码偶发", "当前无报码"],
                                    }
                                ],
                            },
                        },
                        tool_call_id="repair_gate_stream_j1939_2",
                    )
                ]
            )

        return ModelResponse(parts=[TextPart(content="__READY_TO_ANSWER__")])

    def broken_main(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        raise RuntimeError("boom-main")

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(broken_main),
        gate_model_override=FunctionModel(gate_llm),
        renderer_model_override=FunctionModel(broken_main),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first_events = asyncio.run(collect_stream_events(service, ChatRequest(message="J1939 通讯故障怎么排查")))
    first_done = next(event for event in first_events if event.type == AgentEventType.DONE)
    first_response = first_done.metadata["response"]
    assert first_response["type"] == "ask_user"

    second_events = asyncio.run(
        collect_stream_events(
            service,
            ChatRequest(
                session_id=first_response["session_id"],
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first_response["ask_user"]["tool_call_id"],
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "ecu_or_system": {"selected": ["东风天龙"], "text": ""},
                            "fault_codes": {"selected": ["当前无报码"], "text": ""},
                            "data_evidence": {"selected": ["J1939 主干电阻"], "text": ""},
                        },
                        "summary_text": "东风天龙；当前无报码；J1939 主干电阻",
                    },
                ),
            ),
        )
    )
    second_done = next(event for event in second_events if event.type == AgentEventType.DONE)
    second_response = second_done.metadata["response"]
    assert second_response["type"] == "ask_user"

    third_events = asyncio.run(
        collect_stream_events(
            service,
            ChatRequest(
                session_id=second_response["session_id"],
                ask_user_answer=AskUserAnswer(
                    tool_call_id=second_response["ask_user"]["tool_call_id"],
                    answer={
                        "scene": "repair_knowledge_followup",
                        "action": "submit",
                        "fields": {
                            "fault_codes": {"selected": ["报码偶发"], "text": ""},
                        },
                        "summary_text": "报码偶发",
                    },
                ),
            ),
        )
    )

    error_event = next(event for event in third_events if event.type == AgentEventType.ERROR)
    assert error_event.message == "系统处理请求时发生错误，请稍后重试。"
    assert error_event.metadata["detail"] == "boom-main"
