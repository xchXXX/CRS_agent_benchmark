from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from app.agent.context import CaseContextManager, CaseContextStore, LoopGuard
from app.schemas.chat import AskUserAnswer, ChatRequest, ChatResponse


def build_manager(tmp_path, **kwargs) -> CaseContextManager:
    store = CaseContextStore(base_dir=str(tmp_path / "case_context"))
    return CaseContextManager(store=store, **kwargs)


def test_case_context_manager_records_doc_search_and_user_answer_slots(tmp_path):
    manager = build_manager(tmp_path, max_selected_docs=2)
    session_id = "case_ctx_doc_search"

    response = ChatResponse(
        type="documents",
        content={
            "query": "东风天锦电路图",
            "summary": "东风天锦整车电路图",
            "filters": {"brand": "东风", "series": "天锦", "model": "KR"},
            "total": 3,
            "returned_count": 3,
            "results": [
                {"file_id": "1", "filename": "东风天锦整车电路图A", "brand": "东风", "series": "天锦"},
                {"file_id": "2", "filename": "东风天锦整车电路图B", "brand": "东风", "series": "天锦"},
                {"file_id": "3", "filename": "东风天锦整车电路图C", "brand": "东风", "series": "天锦"},
            ],
        },
        session_id=session_id,
        business="DOC_SEARCH",
    )

    context = manager.record_doc_search_response(
        manager.load(session_id),
        request=ChatRequest(message="东风天锦电路图"),
        response=response,
    )
    saved = manager.save(context)

    assert saved.revision == 1
    assert saved.slots.brand == "东风"
    assert saved.slots.series == "天锦"
    assert saved.slots.model == "KR"
    assert saved.slots.selected_doc_ids == ["1", "2"]
    assert saved.slots.selected_doc_titles == ["东风天锦整车电路图A", "东风天锦整车电路图B"]

    answer = AskUserAnswer(
        tool_call_id="ask_user_param_1",
        answer="EDC17C53",
        metadata={"selection_payload": {"filters": {"param_source_id": "159", "ecu_model": "EDC17C53"}}},
    )
    updated = manager.save(manager.record_user_answer(manager.load(session_id), answer, business="PARAM_QUERY"))

    assert updated.revision == 2
    assert updated.slots.parameter_source_id == "159"
    assert updated.slots.ecu_model == "EDC17C53"


def test_case_context_manager_extracts_parameter_result_from_run_messages(tmp_path):
    manager = build_manager(tmp_path)

    run_messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    "query_parameters",
                    {"query": "K46 是什么作用"},
                    tool_call_id="param_query_1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "query_parameters",
                    {
                        "status": "ok",
                        "data": {
                            "matched": True,
                            "query": "K46 是什么作用",
                            "requested_field": "pin_definition",
                            "requested_field_label": "针脚定义",
                            "selected_source": {
                                "id": "159",
                                "title": "EDC17C53针脚电压(12V系统)",
                                "ecu_name": "EDC17C53",
                            },
                            "rows": [
                                {
                                    "id": "1",
                                    "ecu_pin_no": "K46",
                                    "pin_definition": "信号",
                                    "requested_value": "信号",
                                }
                            ],
                            "source_refs": [{"id": "159", "title": "EDC17C53针脚电压(12V系统)"}],
                        },
                    },
                    tool_call_id="param_query_1",
                )
            ]
        )
    ]

    context = manager.record_run_messages(manager.load("case_ctx_run_messages"), run_messages=run_messages)

    assert context.slots.parameter_source_id == "159"
    assert context.slots.ecu_model == "EDC17C53"
    assert len(context.artifacts) == 1
    assert context.artifacts[0].summary.startswith("参数命中")
    assert len(context.attempted_actions) == 1
    assert context.attempted_actions[0].action == "query_parameters"
    assert context.attempted_actions[0].info_gain == "medium"
    assert "ecu_model" in context.attempted_actions[0].filled_slots
    assert context.candidate_answer is not None
    assert context.answer_ready is True


def test_case_context_manager_tracks_no_gain_and_remaining_budget(tmp_path):
    manager = build_manager(tmp_path)
    session_id = "case_ctx_no_gain"
    guard = LoopGuard(
        max_tool_calls=5,
        max_external_tool_calls=2,
        max_ask_user_calls=1,
        max_same_tool_repeat=5,
        max_same_args_repeat=5,
        max_no_gain_streak=3,
    )

    context = manager.attach_runtime_state(manager.load(session_id), loop_guard=guard)
    context = manager.save(context)

    for idx in range(2):
        guard.before_tool_call("query_parameters", {"query": f"K4{idx} 是什么作用"})
        guard.after_tool_call("query_parameters", {"status": "failed", "data": {"message": "参数资料暂不可用。"}})
        context = manager.record_run_messages(
            context,
            run_messages=[
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            "query_parameters",
                            {"query": f"K4{idx} 是什么作用"},
                            tool_call_id=f"param_query_{idx}",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            "query_parameters",
                            {"status": "failed", "data": {"message": "参数资料暂不可用。"}},
                            tool_call_id=f"param_query_{idx}",
                        )
                    ]
                ),
            ],
            loop_guard=guard,
        )
        context = manager.save(context)

    loaded = manager.load(session_id)

    assert loaded.no_gain_streak == 2
    assert loaded.remaining_budget.tool_calls_left == 3
    assert loaded.answer_ready is False
    assert len(loaded.attempted_actions) == 2
    assert loaded.attempted_actions[-1].info_gain == "none"


def test_case_context_manager_compacts_artifacts_and_budget(tmp_path):
    manager = build_manager(
        tmp_path,
        max_artifacts_total=3,
        max_artifacts_per_type=2,
        max_serialized_bytes=1200,
    )
    session_id = "case_ctx_compact"
    context = manager.load(session_id)

    for idx in range(5):
        context = manager.record_user_answer(
            context,
            AskUserAnswer(
                tool_call_id=f"ask_{idx}",
                answer="X" * 240,
                metadata={"selection_payload": {"filters": {"series": f"天锦{idx}"}}},
            ),
        )
        context = manager.save(context)

    loaded = manager.load(session_id)
    artifact_ids = {artifact.artifact_id for artifact in loaded.artifacts}

    assert len(loaded.artifacts) <= 2
    assert loaded.budgets.artifact_count == len(loaded.artifacts)
    assert loaded.budgets.serialized_bytes <= 1200
    assert set(loaded.latest_by_type.values()).issubset(artifact_ids)


def test_case_context_manager_records_image_evidence_slots(tmp_path):
    manager = build_manager(tmp_path)
    session_id = "case_ctx_image_evidence"

    context = manager.record_image_evidence(
        manager.load(session_id),
        evidence={
            "image_evidence_id": "img_001",
            "scene": "vehicle_identity",
            "summary": "识别到东风天锦国六车型铭牌信息。",
            "vehicle": {
                "brand": "东风",
                "series": "天锦",
                "model": "KR",
                "engine": "DDi75E350-60",
                "emission": "国六",
            },
            "diagnosis": {
                "fault_codes": ["P20EE"],
                "descriptions": ["SCR 效率低于阈值"],
                "ecu_model": "EDC17CV44",
            },
            "visible_text": ["东风商用车", "国六"],
            "confidence": 0.92,
        },
    )
    saved = manager.save(context)

    assert saved.slots.brand == "东风"
    assert saved.slots.series == "天锦"
    assert saved.slots.model == "KR"
    assert saved.slots.engine == "DDi75E350-60"
    assert saved.slots.emission == "国六"
    assert saved.slots.fault_code == "P20EE"
    assert saved.slots.ecu_model == "EDC17CV44"
    assert saved.slots.symptom == "SCR 效率低于阈值"
    assert saved.task_type == "FAULT_DIAGNOSIS"
    assert saved.artifacts[-1].type.value == "image_evidence"
