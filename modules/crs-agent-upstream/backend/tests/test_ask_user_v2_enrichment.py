import asyncio

from app.agent.adapters.repair_knowledge_followup_adapter import RepairKnowledgeFollowupAdapter
from app.agent.ask_user_v2.normalizer import normalize_ask_user_question_v2, normalize_ask_user_question_v2_async
from app.agent.ask_user_v2.smart_option_enricher import (
    RepairFollowupFieldPlan,
    RepairFollowupOptionSuggestion,
    RepairFollowupPlanSuggestion,
    SmartAskUserCandidate,
    SmartAskUserFieldSuggestion,
    SmartAskUserOptionEnricher,
    smart_ask_user_option_enricher,
)
from app.agent.domain.repair_knowledge.review import review_repair_answer_gate, review_repair_answer_gate_async
from app.agent.models.ask_user import AskUserInputType, AskUserQuestion


def test_smart_option_enricher_builds_excavator_model_candidates_with_fallback():
    enricher = SmartAskUserOptionEnricher(model_override="test")
    ask_user = AskUserQuestion(
        tool_call_id="ask_user_excavator_model",
        question="请补充您的雷沃挖掘机具体型号或吨位，以便我为您查找正确的诊断口位置。",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={"query": "雷沃挖机检测口在那里？"},
    )

    suggestion = enricher.maybe_build_field_suggestion(ask_user=ask_user)

    assert suggestion is not None
    assert suggestion.field_label == "挖机型号或吨位"
    assert [item.label for item in suggestion.options] == [
        "6 吨级",
        "15 吨级",
        "20 到 22 吨级",
        "30 吨级以上",
    ]


def test_smart_option_enricher_builds_vehicle_info_candidates_with_fallback():
    enricher = SmartAskUserOptionEnricher(model_override="test")
    ask_user = AskUserQuestion(
        tool_call_id="ask_user_vehicle_info",
        question="请补充品牌车系和发动机信息",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={"query": "车报码了", "scene": "generic_ask_user"},
    )

    suggestion = enricher.maybe_build_field_suggestion(ask_user=ask_user)

    assert suggestion is not None
    assert suggestion.field_label == "品牌/车系/发动机信息"
    assert [item.label for item in suggestion.options] == [
        "东风",
        "解放",
        "重汽",
        "陕汽",
        "福田",
    ]


def test_normalize_ask_user_question_v2_upgrades_text_prompt_to_form(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)

    ask_user = AskUserQuestion(
        tool_call_id="ask_user_upgrade_to_form",
        question="请补充您的雷沃挖掘机具体型号或吨位，以便我为您查找正确的诊断口位置。",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={"query": "雷沃挖机检测口在那里？"},
    )

    normalized = normalize_ask_user_question_v2(ask_user)
    form = normalized.context["form"]
    field = form["sections"][0]["fields"][0]

    assert normalized.context["schema_version"] == "2.0"
    assert normalized.context["card_type"] == "ask_form_v2"
    assert field["field_type"] == "single_select"
    assert field["answer_mode"] == "select_or_text"
    assert [item["label"] for item in field["options"]] == [
        "6 吨级",
        "15 吨级",
        "20 到 22 吨级",
        "30 吨级以上",
    ]


def test_normalize_ask_user_question_v2_async_upgrades_text_prompt_to_form(monkeypatch):
    async def fake_suggestion(**kwargs):
        return SmartAskUserFieldSuggestion(
            title="参数查询补充",
            field_label="请补充 ECU / 控制器型号",
            input_hint="也可以补充车型或发动机信息",
            options=[
                SmartAskUserCandidate(label="EDC17CV44"),
                SmartAskUserCandidate(label="EDC17C53"),
            ],
        )

    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "maybe_build_field_suggestion_async",
        fake_suggestion,
        raising=False,
    )

    ask_user = AskUserQuestion(
        tool_call_id="ask_user_async_upgrade_to_form",
        question="请补充 ECU / 控制器型号",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={"query": "请补充 ECU / 控制器型号"},
    )

    normalized = asyncio.run(normalize_ask_user_question_v2_async(ask_user))
    field = normalized.context["form"]["sections"][0]["fields"][0]

    assert normalized.context["smart_options_generated"] is True
    assert field["field_type"] == "single_select"
    assert field["answer_mode"] == "select_or_text"
    assert [item["label"] for item in field["options"]] == ["EDC17CV44", "EDC17C53"]


def test_normalize_ask_user_question_v2_enriches_existing_single_field_form_without_options(monkeypatch):
    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "maybe_build_field_suggestion",
        lambda **kwargs: SmartAskUserFieldSuggestion(
            title="参数查询补充",
            field_label="请补充 ECU / 控制器型号",
            input_hint="也可以补充车型或发动机信息",
            options=[
                SmartAskUserCandidate(label="EDC17CV44"),
                SmartAskUserCandidate(label="EDC17C53"),
            ],
        ),
        raising=False,
    )

    ask_user = AskUserQuestion(
        tool_call_id="ask_user_existing_form_upgrade",
        question="请补充 ECU / 控制器型号",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={
            "schema_version": "2.0",
            "card_type": "ask_form_v2",
            "scene": "parameter_query",
            "form": {
                "form_id": "f1",
                "version": "2.0",
                "mode": "single_page",
                "title": "参数查询补充",
                "description": "请直接补充 ECU / 控制器型号。",
                "ask_reason": "当前还无法从本地资料中唯一定位 ECU 或资料来源。",
                "sections": [
                    {
                        "id": "core",
                        "title": "参数查询补充",
                        "fields": [
                            {
                                "key": "source",
                                "label": "请补充 ECU / 控制器型号",
                                "field_type": "text",
                                "answer_mode": "text_only",
                                "required": True,
                                "required_level": "hard",
                                "options": [],
                                "manual_input": {"enabled": True},
                            }
                        ],
                    }
                ],
                "ui_policy": {},
                "actions": [],
                "validation_policy": {},
            },
        },
    )

    normalized = normalize_ask_user_question_v2(ask_user)
    field = normalized.context["form"]["sections"][0]["fields"][0]

    assert normalized.context["smart_options_generated"] is True
    assert field["field_type"] == "single_select"
    assert field["answer_mode"] == "select_or_text"
    assert [item["label"] for item in field["options"]] == ["EDC17CV44", "EDC17C53"]


def test_normalize_ask_user_question_v2_upgrades_vehicle_info_prompt_without_options(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)

    ask_user = AskUserQuestion(
        tool_call_id="ask_user_vehicle_info_upgrade",
        question="请补充品牌车系和发动机信息",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={"query": "报码怎么分析", "scene": "generic_ask_user"},
    )

    normalized = normalize_ask_user_question_v2(ask_user)
    field = normalized.context["form"]["sections"][0]["fields"][0]

    assert normalized.context["smart_options_generated"] is True
    assert field["field_type"] == "single_select"
    assert field["answer_mode"] == "select_or_text"
    assert [item["label"] for item in field["options"]] == [
        "东风",
        "解放",
        "重汽",
        "陕汽",
        "福田",
    ]


def test_repair_followup_adapter_builds_model_presets_for_location_query(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)

    presets = RepairKnowledgeFollowupAdapter._build_presets(
        key="ecu_or_system",
        query="雷沃挖机检测口在那里？",
        loaded_context=None,
    )

    assert presets == [
        "6 吨级",
        "15 吨级",
        "20 到 22 吨级",
        "30 吨级以上",
    ]


def test_location_lookup_query_uses_equipment_info_label_and_hint(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)

    group = RepairKnowledgeFollowupAdapter._build_group_for_key(
        key="ecu_or_system",
        label="挖掘机相关信息",
        query="雷沃挖机检测口在那里？",
        loaded_context=None,
    )

    assert group["label"] == "挖机型号或吨位"
    assert group["hint"] == "设备型号或吨位越准确，诊断口位置越容易定位。"
    assert group["placeholder"] == "例如：FR60E2-HD、FR150E2，或 20 吨级"


def test_location_lookup_review_only_requests_equipment_info(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)

    review = review_repair_answer_gate(
        query="雷沃挖机检测口在那里？",
        loaded_context=None,
    )

    assert review.ask_user is None or all(
        group["key"] == "ecu_or_system"
        for group in (review.ask_user.context.get("field_groups") or [])
    )


def test_repair_followup_adapter_uses_model_presets_for_open_symptom_query(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "fake-model", raising=False)
    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_option_labels",
        lambda **kwargs: [
            "启动时轨压建立不上去",
            "怠速轨压偏低",
            "加速时轨压跟不上目标值",
            "冷车明显热车减轻",
        ],
        raising=False,
    )

    presets = RepairKnowledgeFollowupAdapter._build_presets(
        key="fault_phenomenon",
        query="高压共轨压力低是什么原因",
        loaded_context=None,
    )

    assert presets == [
        "启动时轨压建立不上去",
        "怠速轨压偏低",
        "加速时轨压跟不上目标值",
        "冷车明显热车减轻",
    ]


def test_repair_followup_adapter_async_uses_model_presets_for_fault_codes(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "fake-model", raising=False)
    async def fake_fault_code_options(**kwargs):
        return [
            "P0087 燃油轨压力过低",
            "P0191 燃油轨压力传感器性能异常",
        ]

    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_option_labels_async",
        fake_fault_code_options,
        raising=False,
    )

    presets = asyncio.run(
        RepairKnowledgeFollowupAdapter._build_presets_async(
            key="fault_codes",
            query="康明斯动力不足怎么办",
            loaded_context=None,
        )
    )

    assert presets == [
        "P0087 燃油轨压力过低",
        "P0191 燃油轨压力传感器性能异常",
    ]


def test_repair_followup_adapter_builds_air_conditioning_data_evidence_group():
    group = RepairKnowledgeFollowupAdapter._build_group_for_key(
        key="data_evidence",
        label="记录相关故障码或观测过压力表",
        query="解放空调不制冷",
        loaded_context=None,
    )

    assert group["label"] == "关键异常观测"
    assert group["hint"] == "优先选择最接近的异常结果，比如低压偏低、高压偏高、出风温度降不下来或风扇不工作。"
    assert group["placeholder"] == "例如：已测高低压，低压约 0.25MPa / 高压约 1.4MPa"
    assert 1 <= len(group["presets"]) <= RepairKnowledgeFollowupAdapter.MAX_FIELD_OPTIONS
    assert not any(any(token in item for token in ("上传", "截图", "文件")) for item in group["presets"])
    assert not any(any(token in item for token in ("已确认", "已核对", "已记录", "已查看", "已测")) for item in group["presets"])


def test_repair_followup_adapter_v2_field_uses_closed_labels_for_data_evidence():
    field = RepairKnowledgeFollowupAdapter._build_v2_field(
        group={
            "key": "data_evidence",
            "label": "关键异常观测",
            "required_level": "hard",
            "selection_mode": "multi",
            "presets": ["高低压压力", "出风口温度", "低压明显偏低"],
            "placeholder": "例如：已测高低压",
            "hint": "优先选择最接近的异常结果。",
        },
        index=0,
    )

    assert [(item.key, item.label) for item in field.options] == [
        ("高低压压力", "高低压压力"),
        ("出风口温度", "出风口温度"),
        ("低压明显偏低", "低压明显偏低"),
    ]


def test_repair_followup_adapter_builds_sensor_system_presets_for_electrical_query():
    group = RepairKnowledgeFollowupAdapter._build_group_for_key(
        key="ecu_or_system",
        label="相关系统信息",
        query="传感器 5V 供电短路怎么查",
        loaded_context=None,
    )

    assert group["label"] == "受影响的传感器/系统"
    assert 1 <= len(group["presets"]) <= RepairKnowledgeFollowupAdapter.MAX_FIELD_OPTIONS
    assert group["hint"] == "先选最可能受影响的传感器或系统支路；不确定具体件名时，也可以先按系统范围点选。"
    assert group["placeholder"] == "例如：油门踏板、轨压传感器、空调压力传感器、BCM 相关支路"


def test_repair_review_open_symptom_query_no_longer_degrades_to_blank_fault_phenomenon(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "fake-model", raising=False)
    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_option_labels",
        lambda **kwargs: (
            [
                "启动时轨压建立不上去",
                "怠速轨压偏低",
                "加速时轨压跟不上目标值",
            ]
            if kwargs.get("field_key") == "fault_phenomenon"
            else []
        ),
        raising=False,
    )

    review = review_repair_answer_gate(
        query="高压共轨压力低是什么原因",
        loaded_context=None,
    )

    assert review.ask_user is not None
    group_map = {group["key"]: group for group in review.ask_user.context.get("field_groups") or []}
    assert group_map["fault_phenomenon"]["presets"] == [
        "启动时轨压建立不上去",
        "怠速轨压偏低",
        "加速时轨压跟不上目标值",
    ]


def test_repair_followup_adapter_prefers_llm_planned_field_groups(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "fake-model", raising=False)
    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_plan",
        lambda **kwargs: RepairFollowupPlanSuggestion(
            ask_reason="先确认最能改变诊断路径的空调现象、触发工况和关键异常观测。",
            fields=[
                RepairFollowupFieldPlan(
                    key="fault_phenomenon",
                    label="当前空调现象",
                    selection_mode="single",
                    options=[
                        SmartAskUserCandidate(label="风量正常但不制冷"),
                        SmartAskUserCandidate(label="开始制冷，热车后变差"),
                        SmartAskUserCandidate(label="怠速不凉，转速上来后稍好"),
                    ],
                ),
                RepairFollowupFieldPlan(
                    key="working_condition",
                    label="不制冷最明显的工况",
                    selection_mode="single",
                    options=[
                        SmartAskUserCandidate(label="热车后明显"),
                        SmartAskUserCandidate(label="怠速原地明显"),
                        SmartAskUserCandidate(label="中午高温时明显"),
                    ],
                ),
                RepairFollowupFieldPlan(
                    key="data_evidence",
                    label="关键异常观测",
                    selection_mode="multi",
                    options=[
                        SmartAskUserCandidate(label="高低压压力"),
                        SmartAskUserCandidate(label="出风口温度"),
                        SmartAskUserCandidate(label="压缩机工作状态"),
                    ],
                ),
            ],
        ),
        raising=False,
    )

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query="解放空调不制冷",
        loaded_context={
            "loaded": True,
            "source_refs": [{"title": "空调不制冷排查"}],
            "entries": [{"content": "先区分现象、工况和已经做过的检查。"}],
            "llm_context": "先区分现象、工况和已经做过的检查。",
        },
        answer_text="先区分现象、工况和已经做过的检查。",
    )

    assert ask_user.context["field_groups_source"] == "llm_plan"
    assert ask_user.context["ask_reason"] == "先确认最能改变诊断路径的空调现象、触发工况和关键异常观测。"
    assert [group["key"] for group in ask_user.context["field_groups"]] == [
        "fault_phenomenon",
        "working_condition",
        "data_evidence",
    ]

    form = ask_user.context["form"]
    first_field = form["sections"][0]["fields"][0]
    assert [option["label"] for option in first_field["options"]] == [
        "风量正常但不制冷",
        "开始制冷，热车后变差",
        "怠速不凉，转速上来后稍好",
    ]
    assert all(option["option_source"] == "llm_predicted" for option in first_field["options"])


def test_repair_review_prefers_llm_planned_field_groups(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "fake-model", raising=False)
    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_plan",
        lambda **kwargs: RepairFollowupPlanSuggestion(
            ask_reason="先判断是轨压建立问题、报码方向，还是只在特定工况下掉压。",
            fields=[
                RepairFollowupFieldPlan(
                    key="fault_phenomenon",
                    label="轨压低时最接近的表现",
                    selection_mode="single",
                    options=[
                        SmartAskUserCandidate(label="启动时轨压建立不上去"),
                        SmartAskUserCandidate(label="怠速轨压偏低"),
                        SmartAskUserCandidate(label="加速时轨压跟不上目标值"),
                    ],
                ),
                RepairFollowupFieldPlan(
                    key="fault_codes",
                    label="当前故障码情况",
                    selection_mode="multi",
                    options=[
                        SmartAskUserCandidate(label="P0087 燃油轨压力过低"),
                        SmartAskUserCandidate(label="P0191 燃油轨压力传感器性能异常"),
                        SmartAskUserCandidate(label="当前无报码"),
                    ],
                ),
            ],
        ),
        raising=False,
    )

    review = review_repair_answer_gate(
        query="高压共轨压力低是什么原因",
        loaded_context=None,
    )

    assert review.ask_user is not None
    assert review.ask_user.context["field_groups_source"] == "llm_plan"
    assert review.ask_user.context["ask_reason"] == "先判断是轨压建立问题、报码方向，还是只在特定工况下掉压。"
    assert [group["key"] for group in review.ask_user.context["field_groups"]] == [
        "fault_phenomenon",
        "fault_codes",
    ]


def test_repair_review_async_prefers_llm_planned_field_groups(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "fake-model", raising=False)
    async def fake_plan_async(**kwargs):
        return RepairFollowupPlanSuggestion(
            ask_reason="先区分现象和已知报码方向。",
            fields=[
                RepairFollowupFieldPlan(
                    key="fault_phenomenon",
                    label="当前最接近的现象",
                    selection_mode="single",
                    options=[
                        SmartAskUserCandidate(label="启动时轨压建立不上去"),
                        SmartAskUserCandidate(label="怠速轨压偏低"),
                    ],
                ),
                RepairFollowupFieldPlan(
                    key="fault_codes",
                    label="当前故障码情况",
                    selection_mode="multi",
                    options=[
                        SmartAskUserCandidate(label="P0087 燃油轨压力过低"),
                        SmartAskUserCandidate(label="当前无报码"),
                    ],
                ),
            ],
        )

    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_plan_async",
        fake_plan_async,
        raising=False,
    )

    review = asyncio.run(
        review_repair_answer_gate_async(
            query="高压共轨压力低是什么原因",
            loaded_context=None,
        )
    )

    assert review.ask_user is not None
    assert review.ask_user.context["field_groups_source"] == "llm_plan"
    assert review.ask_user.context["ask_reason"] == "先区分现象和已知报码方向。"
    assert [group["key"] for group in review.ask_user.context["field_groups"]] == [
        "fault_phenomenon",
        "fault_codes",
    ]


def test_repair_followup_adapter_keeps_llm_planned_field_without_presets(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "test", raising=False)
    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_plan",
        lambda **kwargs: RepairFollowupPlanSuggestion(
            ask_reason="先确认最关键的车型或系统信息。",
            fields=[
                RepairFollowupFieldPlan(
                    key="ecu_or_system",
                    label="涉及的车型或系统",
                    selection_mode="mixed",
                    placeholder="例如：解放 J6P + 潍柴 WP13",
                    hint="如果没有合适候选，直接补充完整车型或系统型号。",
                    options=[],
                ),
            ],
        ),
        raising=False,
    )

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query="SCR 系统故障",
        loaded_context={
            "loaded": True,
            "source_refs": [{"title": "SCR 排查"}],
            "entries": [{"content": "先确认车型和系统范围。"}],
            "llm_context": "先确认车型和系统范围。",
        },
        answer_text="先确认车型和系统范围。",
    )

    assert ask_user.context["field_groups_source"] == "llm_plan"
    assert [group["key"] for group in ask_user.context["field_groups"]] == ["ecu_or_system"]
    field = ask_user.context["form"]["sections"][0]["fields"][0]
    assert field["field_type"] == "single_select"
    assert field["answer_mode"] == "select_or_text"
    assert len(field["options"]) > 0
    assert all("上传" not in option["label"] and "截图" not in option["label"] for option in field["options"])


def test_repair_followup_option_normalizer_filters_process_upload_style_items():
    enricher = SmartAskUserOptionEnricher(model_override="test")

    normalized = enricher._normalize_repair_followup_prediction(
        RepairFollowupOptionSuggestion(
            options=[
                SmartAskUserCandidate(label="上传报码截图"),
                SmartAskUserCandidate(label="导出数据流文件"),
                SmartAskUserCandidate(label="报码状态已确认"),
                SmartAskUserCandidate(label="报码状态已确认"),
                SmartAskUserCandidate(label="已测 J1939 主干电阻"),
                SmartAskUserCandidate(label="报码集中在通讯/离线类"),
            ]
        )
    )

    assert normalized == ["报码集中在通讯/离线类"]


def test_repair_followup_plan_normalizer_filters_process_upload_style_items():
    enricher = SmartAskUserOptionEnricher(model_override="test")

    normalized = enricher._normalize_repair_followup_plan_prediction(
        RepairFollowupPlanSuggestion(
            ask_reason="先确认关键异常观测。",
            fields=[
                RepairFollowupFieldPlan(
                    key="data_evidence",
                    label="关键异常观测",
                    selection_mode="multi",
                    options=[
                        SmartAskUserCandidate(label="上传故障码截图"),
                        SmartAskUserCandidate(label="关键数据已核对"),
                        SmartAskUserCandidate(label="轨压跟不上目标值"),
                        SmartAskUserCandidate(label="数据流暂未见明显异常"),
                    ],
                )
            ],
        )
    )

    assert normalized is not None
    assert [item.label for item in normalized.fields[0].options] == [
        "轨压跟不上目标值",
        "数据流暂未见明显异常",
    ]


def test_repair_followup_plan_normalizer_no_longer_truncates_to_four_fields():
    enricher = SmartAskUserOptionEnricher(model_override="test")

    normalized = enricher._normalize_repair_followup_plan_prediction(
        RepairFollowupPlanSuggestion(
            ask_reason="按信息增益从高到低连续追问。",
            fields=[
                RepairFollowupFieldPlan(
                    key=key,
                    label=label,
                    selection_mode="single",
                    options=[SmartAskUserCandidate(label=option)],
                )
                for key, label, option in [
                    ("fault_phenomenon", "当前最明显现象", "启动时轨压建立不上去"),
                    ("working_condition", "最明显工况", "热车后明显"),
                    ("fault_codes", "当前报码", "P0087 燃油轨压力过低"),
                    ("ecu_or_system", "涉事系统", "高压共轨系统"),
                    ("data_evidence", "关键异常观测", "轨压跟不上目标值"),
                    ("repair_history", "近期维修影响", "近期更换过燃油滤芯"),
                ]
            ],
        )
    )

    assert normalized is not None
    assert len(normalized.fields) == 6


def test_repair_followup_llm_planned_options_are_not_replaced_by_static_candidates(monkeypatch):
    monkeypatch.setattr(smart_ask_user_option_enricher, "_model_override", "fake-model", raising=False)
    monkeypatch.setattr(
        smart_ask_user_option_enricher,
        "suggest_repair_followup_plan",
        lambda **kwargs: RepairFollowupPlanSuggestion(
            ask_reason="先按外在表现判断 SCR 故障分型。",
            fields=[
                RepairFollowupFieldPlan(
                    key="fault_phenomenon",
                    label="当前最明显的后处理表现",
                    selection_mode="single",
                    options=[
                        SmartAskUserCandidate(label="排气管外壁有尿素结晶"),
                        SmartAskUserCandidate(label="报码后车辆限扭"),
                        SmartAskUserCandidate(label="尿素泵建压失败"),
                    ],
                )
            ],
        ),
        raising=False,
    )

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query="SCR 系统故障",
        loaded_context={
            "loaded": True,
            "source_refs": [{"title": "SCR 排查"}],
            "entries": [{"content": "先按外在表现判断 SCR 故障分型。"}],
            "llm_context": "先按外在表现判断 SCR 故障分型。",
        },
        answer_text="先按外在表现判断 SCR 故障分型。",
    )

    field = ask_user.context["form"]["sections"][0]["fields"][0]
    assert [option["label"] for option in field["options"]] == [
        "排气管外壁有尿素结晶",
        "报码后车辆限扭",
        "尿素泵建压失败",
    ]
