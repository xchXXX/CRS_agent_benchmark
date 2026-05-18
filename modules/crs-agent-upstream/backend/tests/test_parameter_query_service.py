from types import SimpleNamespace

from datetime import datetime

from app.agent.domain.parameter_query.index_store import ParameterKnowledgeIndex, ParameterQueryIndexStore
from app.agent.domain.parameter_query.llm_normalizer import (
    ParameterQueryIntent,
    ParameterQueryRowCandidate,
    ParameterQueryRowSelection,
    ParameterQuerySourceCandidate,
    PydanticAIParameterQueryNormalizer,
)
from app.agent.domain.parameter_query.models import AliasEntry, ParameterIndexRow, ParameterIndexSource
from app.agent.domain.parameter_query.parser import parse_markdown_pin_rows
from app.agent.domain.parameter_query.service import ParameterQueryService


class DummyExternalRepository:
    def list_pin_info_sources(self):
        return []

    def fetch_contents(self, source_ids):
        return {}


class FakeLLMNormalizer:
    def interpret_query(
        self,
        *,
        query: str,
        candidate_sources,
        selected_source_id=None,
        selected_source_title=None,
    ):
        lowered = query.lower()
        if selected_source_id == 159 or "edc17c53" in lowered:
            missing_target = "针脚定义" in query and not any(token in lowered for token in ("k46", "can0h"))
            return ParameterQueryIntent(
                ecu_source_id=159,
                ecu_text="EDC17C53",
                requested_field="pin_definition",
                target_text=None if missing_target else "K46" if "k46" in lowered else "CAN0H",
                target_type="unknown" if missing_target else "ecu_pin_no" if "k46" in lowered else "signal",
                need_clarify=missing_target,
                clarify_target="target" if missing_target else "none",
                reason="fake_edc17c53",
            )
        if "sid208" in lowered:
            target = "C244" if "c244" in lowered else "Z999" if "z999" in lowered else None
            return ParameterQueryIntent(
                ecu_source_id=120,
                ecu_text="SID208",
                requested_field="pin_definition",
                target_text=target,
                target_type="ecu_pin_no" if target else "unknown",
                reason="fake_sid208",
            )
        if "abc999" in lowered:
            return ParameterQueryIntent(
                ecu_source_id=None,
                ecu_text="ABC999",
                requested_field="pin_definition",
                target_text="K46",
                target_type="ecu_pin_no",
                reason="fake_unknown_ecu",
            )
        if "canh" in lowered:
            return ParameterQueryIntent(
                ecu_source_id=None,
                candidate_source_ids=[159, 177],
                ecu_text=None,
                requested_field="ecu_pin_no",
                target_text="CANH",
                target_type="signal",
                need_clarify=True,
                clarify_target="ecu",
                reason="fake_missing_ecu",
            )
        return ParameterQueryIntent(
            ecu_source_id=None,
            candidate_source_ids=[],
            ecu_text=None,
            requested_field="pin_definition",
            target_text=None,
            target_type="unknown",
            need_clarify=True,
            clarify_target="ecu",
            reason="fake_default",
        )

    def select_rows(
        self,
        *,
        query: str,
        source_title: str,
        source_ecu_name: str | None,
        requested_field: str | None,
        target_text: str | None,
        target_type: str,
        rows,
    ):
        del query, source_title, source_ecu_name, requested_field, target_type
        if target_text == "CAN0H":
            return ParameterQueryRowSelection(match_state="exact_match", row_ids=[3], reason="fake_can0h")
        if not target_text:
            return ParameterQueryRowSelection(match_state="missing_target", row_ids=[], reason="fake_missing")
        return ParameterQueryRowSelection(match_state="pin_not_found", row_ids=[], reason="fake_not_found")


class FakeOverClarifyingLLMNormalizer(FakeLLMNormalizer):
    def interpret_query(
        self,
        *,
        query: str,
        candidate_sources,
        selected_source_id=None,
        selected_source_title=None,
    ):
        lowered = query.lower()
        if "sid208" in lowered:
            return ParameterQueryIntent(
                ecu_source_id=None,
                candidate_source_ids=[102, 120, 177],
                ecu_text=None,
                requested_field="pin_definition",
                target_text="C244" if "c244" in lowered else None,
                target_type="ecu_pin_no",
                need_clarify=True,
                clarify_target="ecu",
                reason="fake_over_clarify_sid208",
            )
        return super().interpret_query(
            query=query,
            candidate_sources=candidate_sources,
            selected_source_id=selected_source_id,
            selected_source_title=selected_source_title,
        )


class FakeInvalidCandidateLLMNormalizer(FakeLLMNormalizer):
    def interpret_query(
        self,
        *,
        query: str,
        candidate_sources,
        selected_source_id=None,
        selected_source_title=None,
    ):
        del query, candidate_sources, selected_source_id, selected_source_title
        return ParameterQueryIntent(
            ecu_source_id=None,
            candidate_source_ids=[999999],
            ecu_text=None,
            requested_field="pin_definition",
            target_text="C244",
            target_type="ecu_pin_no",
            need_clarify=True,
            clarify_target="ecu",
            reason="fake_invalid_candidates",
        )


def test_parameter_query_normalizer_interpret_query_sync_uses_agent_output(monkeypatch):
    normalizer = PydanticAIParameterQueryNormalizer(model_override="openrouter:google/gemini-3.1-flash-lite-preview")
    expected = ParameterQueryIntent(
        ecu_source_id=6,
        ecu_text="OH6",
        component_text="风扇离合器",
        requested_field="voltage",
        target_text="风扇离合器",
        target_type="component",
        reason="agent_output",
    )

    monkeypatch.setattr(
        normalizer,
        "_get_intent_agent",
        lambda **_: SimpleNamespace(run_sync=lambda user_prompt: SimpleNamespace(output=expected)),
    )

    result = normalizer.interpret_query(
        query="OH6 风扇离合器电压多少",
        candidate_sources=[
            ParameterQuerySourceCandidate(
                source_id=6,
                title="OH6针脚电压(24V系统)",
                ecu_name="OH6",
                system_voltage=24,
                row_count=120,
            )
        ],
    )

    assert result.ecu_source_id == 6
    assert result.target_type == "component"
    assert result.target_text == "风扇离合器"
    assert result.requested_field == "voltage"


def test_parameter_query_normalizer_select_rows_sync_uses_agent_output(monkeypatch):
    normalizer = PydanticAIParameterQueryNormalizer(model_override="openrouter:google/gemini-3.1-flash-lite-preview")
    expected = ParameterQueryRowSelection(match_state="exact_match", row_ids=[14667], reason="agent_output")

    monkeypatch.setattr(
        normalizer,
        "_get_row_agent",
        lambda **_: SimpleNamespace(run_sync=lambda user_prompt: SimpleNamespace(output=expected)),
    )

    result = normalizer.select_rows(
        query="OH6 风扇离合器电压多少",
        source_title="OH6针脚电压(24V系统)",
        source_ecu_name="OH6",
        requested_field="voltage",
        target_text="风扇离合器",
        target_type="component",
        rows=[
            ParameterQueryRowCandidate(
                row_id=14667,
                ecu_pin_no="AA2",
                component_name="风扇离合器/硅油风扇",
                pin_definition="控制",
                open_voltage_text="0V",
            )
        ],
    )

    assert result.match_state == "exact_match"
    assert result.row_ids == [14667]


def build_index() -> ParameterKnowledgeIndex:
    source_159 = ParameterIndexSource(
        source_knowledge_id=159,
        title="EDC17C53针脚电压(12V系统)",
        title_normalized="edc17c53针脚电压12v系统",
        ecu_name="EDC17C53",
        ecu_name_normalized="edc17c53",
        system_voltage=12,
        pin_doc_kind="pin_voltage",
        parsed_row_count=3,
        raw_content=None,
        last_synced_at=None,
    )
    source_177 = ParameterIndexSource(
        source_knowledge_id=177,
        title="MD1CE100针脚电压(24V系统)",
        title_normalized="md1ce100针脚电压24v系统",
        ecu_name="MD1CE100",
        ecu_name_normalized="md1ce100",
        system_voltage=24,
        pin_doc_kind="pin_voltage",
        parsed_row_count=1,
        raw_content=None,
        last_synced_at=None,
    )
    source_120 = ParameterIndexSource(
        source_knowledge_id=120,
        title="大陆马牌SID208针脚电压(12V系统)",
        title_normalized="大陆马牌sid208针脚电压12v系统",
        ecu_name="大陆马牌SID208",
        ecu_name_normalized="大陆马牌sid208",
        system_voltage=12,
        pin_doc_kind="pin_voltage",
        parsed_row_count=2,
        raw_content=None,
        last_synced_at=None,
    )
    row_1 = ParameterIndexRow(
        id=1,
        source_knowledge_id=159,
        source_title=source_159.title,
        ecu_name="EDC17C53",
        ecu_name_normalized="edc17c53",
        system_voltage=12,
        row_no=1,
        component_name="点火开关T15",
        component_name_normalized="点火开关t15",
        ecu_pin_no="K46",
        ecu_pin_no_normalized="K46",
        pin_definition="信号",
        pin_definition_normalized="信号",
        connector_pin_no=None,
        open_voltage_text="12V",
        static_voltage_text="12V",
        idle_voltage_text="12V",
        remark=None,
        raw_row_json=None,
        search_text="EDC17C53 点火开关T15 K46 信号 12V",
    )
    row_2 = ParameterIndexRow(
        id=2,
        source_knowledge_id=159,
        source_title=source_159.title,
        ecu_name="EDC17C53",
        ecu_name_normalized="edc17c53",
        system_voltage=12,
        row_no=2,
        component_name="CAN2",
        component_name_normalized="can2",
        ecu_pin_no="K86",
        ecu_pin_no_normalized="K86",
        pin_definition="CAN2H",
        pin_definition_normalized="can2h",
        connector_pin_no=None,
        open_voltage_text="2.5V",
        static_voltage_text="2.5V",
        idle_voltage_text="2.5V",
        remark="集成120Ω电阻",
        raw_row_json=None,
        search_text="EDC17C53 CAN2 K86 CAN2H 2.5V",
    )
    row_3 = ParameterIndexRow(
        id=3,
        source_knowledge_id=177,
        source_title=source_177.title,
        ecu_name="MD1CE100",
        ecu_name_normalized="md1ce100",
        system_voltage=24,
        row_no=1,
        component_name="CAN0",
        component_name_normalized="can0",
        ecu_pin_no="4.29",
        ecu_pin_no_normalized="429",
        pin_definition="CAN0H",
        pin_definition_normalized="can0h",
        connector_pin_no=None,
        open_voltage_text="2.5V",
        static_voltage_text="2.5V",
        idle_voltage_text="2.5V",
        remark="ECU内电阻：120Ω",
        raw_row_json=None,
        search_text="MD1CE100 CAN0 4.29 CAN0H 2.5V",
    )
    row_4 = ParameterIndexRow(
        id=4,
        source_knowledge_id=120,
        source_title=source_120.title,
        ecu_name="大陆马牌SID208",
        ecu_name_normalized="大陆马牌sid208",
        system_voltage=12,
        row_no=1,
        component_name="曲轴位置传感器(CKP)",
        component_name_normalized="曲轴位置传感器ckp",
        ecu_pin_no="C2-44",
        ecu_pin_no_normalized="C244",
        pin_definition="接地",
        pin_definition_normalized="接地",
        connector_pin_no=None,
        open_voltage_text="0V",
        static_voltage_text="0V",
        idle_voltage_text="0V",
        remark=None,
        raw_row_json=None,
        search_text="SID208 曲轴位置传感器 C2-44 接地 0V",
    )
    row_5 = ParameterIndexRow(
        id=5,
        source_knowledge_id=159,
        source_title=source_159.title,
        ecu_name="EDC17C53",
        ecu_name_normalized="edc17c53",
        system_voltage=12,
        row_no=3,
        component_name="0H6风扇离合器",
        component_name_normalized="0h6风扇离合器",
        ecu_pin_no="K90",
        ecu_pin_no_normalized="K90",
        pin_definition="PWM控制",
        pin_definition_normalized="pwm控制",
        connector_pin_no=None,
        open_voltage_text="12V",
        static_voltage_text="12V",
        idle_voltage_text="8V",
        remark=None,
        raw_row_json=None,
        search_text="EDC17C53 0H6风扇离合器 K90 PWM控制 12V",
    )
    alias_lookup = {
        "ecu": {
            "edc17c53": (
                AliasEntry("ecu", "EDC17C53", "edc17c53", "EDC17C53", "edc17c53", 200, "generated", 159),
            ),
            "md1ce100": (
                AliasEntry("ecu", "MD1CE100", "md1ce100", "MD1CE100", "md1ce100", 200, "generated", 177),
            ),
            "sid208": (
                AliasEntry("ecu", "大陆马牌SID208", "大陆马牌sid208", "SID208", "sid208", 165, "generated", 120),
            ),
        }
    }
    return ParameterKnowledgeIndex(
        built_at=datetime.utcnow(),
        sources_by_id={120: source_120, 159: source_159, 177: source_177},
        rows_by_id={1: row_1, 2: row_2, 3: row_3, 4: row_4, 5: row_5},
        rows_by_source={120: (row_4,), 159: (row_1, row_2, row_5), 177: (row_3,)},
        rows_by_pin={"K46": (row_1,), "K86": (row_2,), "429": (row_3,), "C244": (row_4,), "K90": (row_5,)},
        rows_by_ecu={"大陆马牌sid208": (row_4,), "edc17c53": (row_1, row_2, row_5), "md1ce100": (row_3,)},
        alias_lookup=alias_lookup,
    )


def build_service() -> ParameterQueryService:
    index_store = ParameterQueryIndexStore()
    index_store.replace(build_index())
    return ParameterQueryService(
        session_factory=lambda: None,
        external_repository=DummyExternalRepository(),
        index_store=index_store,
        llm_normalizer=FakeLLMNormalizer(),
    )


def build_service_with_over_clarifying_llm() -> ParameterQueryService:
    index_store = ParameterQueryIndexStore()
    index_store.replace(build_index())
    return ParameterQueryService(
        session_factory=lambda: None,
        external_repository=DummyExternalRepository(),
        index_store=index_store,
        llm_normalizer=FakeOverClarifyingLLMNormalizer(),
    )


def build_service_with_invalid_candidate_llm() -> ParameterQueryService:
    index_store = ParameterQueryIndexStore()
    index_store.replace(build_index())
    return ParameterQueryService(
        session_factory=lambda: None,
        external_repository=DummyExternalRepository(),
        index_store=index_store,
        llm_normalizer=FakeInvalidCandidateLLMNormalizer(),
    )


def test_parse_markdown_pin_rows_extracts_expected_columns():
    markdown = """
|零部件|ECU针脚编号|针脚定义|接插件针脚号|开路电压（V）|连接线束后静态电压|低怠速电压|备注|
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
|点火开关T15|K46|信号||12V|12V|12V| |
|CAN2|K86|CAN2H||2.5V|2.5V|2.5V|集成120Ω电阻|
"""
    rows = parse_markdown_pin_rows(markdown)

    assert len(rows) == 2
    assert rows[0].component_name == "点火开关T15"
    assert rows[0].ecu_pin_no == "K46"
    assert rows[0].pin_definition == "信号"
    assert str(rows[1].open_voltage_min) == "2.5"
    assert rows[1].remark == "集成120Ω电阻"


def test_parameter_query_service_matches_exact_pin_definition():
    service = build_service()

    result = service.query("EDC17C53 的 K46 是什么作用")

    assert result["status"] == "ok"
    assert result["data"]["matched"] is True
    assert result["data"]["selected_source"]["id"] == "159"
    assert result["data"]["rows"][0]["ecu_pin_no"] == "K46"
    assert result["data"]["rows"][0]["requested_value"] == "信号"


def test_parameter_query_service_returns_source_clarify_when_ecu_missing():
    service = build_service()

    result = service.query("CANH 在哪个针脚")

    assert result["status"] == "need_clarify"
    assert result["clarify"]["question"] == "请先确认 ECU 型号"
    assert len(result["clarify"]["options"]) >= 2


def test_parameter_query_service_returns_row_clarify_when_ecu_has_no_pin_target():
    service = build_service()

    result = service.query("EDC17C53 的针脚定义是什么")

    assert result["status"] == "need_clarify"
    assert result["data"]["clarify_type"] == "row"
    assert result["data"]["reason"] == "missing_target"
    assert result["clarify"]["question"] == "请补充要查的具体针脚，例如 K46、K86、K90"
    assert result["clarify"]["options"] == []
    assert result["clarify"]["context"]["source_id"] == "159"
    assert result["clarify"]["context"]["input_hint"] == "请按当前 ECU 的针脚格式输入，例如：K46、K86、K90"
    assert result["clarify"]["context"]["pin_examples"] == ["K46", "K86", "K90"]


def test_parse_markdown_pin_rows_keeps_distinct_multi_segment_pin_numbers():
    markdown = """
|零部件|ECU针脚编号|针脚定义|接插件针脚号|开路电压（V）|连接线束后静态电压|低怠速电压|备注|
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
|曲轴位置传感器(CKP)|C2-38|供电||5V|5V|5V||
|曲轴位置传感器(CKP)|C2-44|接地||0V|0V|0V||
"""
    rows = parse_markdown_pin_rows(markdown)

    assert rows[0].ecu_pin_no == "C2-38"
    assert rows[1].ecu_pin_no == "C2-44"


def test_parameter_query_service_prefers_short_ecu_alias_over_vendor_prefixed_title():
    service = build_service()

    result = service.query("SID208 C244 引脚是什么作用")

    assert result["status"] == "ok"
    assert result["data"]["matched"] is True
    assert result["data"]["selected_source"]["id"] == "120"
    assert result["data"]["rows"][0]["ecu_pin_no"] == "C2-44"


def test_parameter_query_service_returns_source_clarify_when_ecu_not_found():
    service = build_service()

    result = service.query("ABC999 K46 引脚是什么作用")

    assert result["status"] == "need_clarify"
    assert result["data"]["matched"] is False
    assert result["data"]["reason"] == "ecu_not_found"
    assert result["clarify"]["question"] == "请确认 ECU 型号"
    assert result["clarify"]["context"]["message"] == "本地参数资料库中暂无“ABC999”相关 ECU 资料，请确认 ECU 型号。"


def test_parameter_query_service_returns_no_match_when_pin_not_found_under_ecu():
    service = build_service()

    result = service.query("SID208 Z999 引脚是什么作用")

    assert result["status"] == "ok"
    assert result["data"]["matched"] is False
    assert result["data"]["reason"] == "pin_not_found_under_ecu"
    assert "请检查针脚输入是否正确" in result["data"]["message"]


def test_parameter_query_service_skips_ecu_clarify_when_query_already_contains_unique_ecu_alias():
    service = build_service_with_over_clarifying_llm()

    result = service.query("SID208 C244 引脚是什么作用")

    assert result["status"] == "ok"
    assert result["data"]["matched"] is True
    assert result["data"]["selected_source"]["id"] == "120"
    assert result["data"]["rows"][0]["ecu_pin_no"] == "C2-44"


def test_parameter_query_service_overrides_stale_selected_source_when_raw_query_has_new_ecu_alias():
    service = build_service_with_over_clarifying_llm()

    result = service.query(
        "EDC17C53 SID208 C244 引脚是什么作用",
        selection_payload={"filters": {"param_source_id": "159"}},
        raw_query="SID208 C244 引脚是什么作用",
    )

    assert result["status"] == "ok"
    assert result["data"]["matched"] is True
    assert result["data"]["selected_source"]["id"] == "120"
    assert result["data"]["rows"][0]["ecu_pin_no"] == "C2-44"


def test_parameter_query_service_recognizes_parenthetical_ecu_alias_prefix():
    source_7 = ParameterIndexSource(
        source_knowledge_id=7,
        title="易控F02(共轨)针脚电压(24V系统)",
        title_normalized="易控f02共轨针脚电压24v系统",
        ecu_name="易控F02(共轨",
        ecu_name_normalized="易控f02共轨",
        system_voltage=24,
        pin_doc_kind="pin_voltage",
        parsed_row_count=1,
        raw_content=None,
        last_synced_at=None,
    )
    source_77 = ParameterIndexSource(
        source_knowledge_id=77,
        title="电装D34(东风EQ4H)针脚电(24V系统)",
        title_normalized="电装d34东风eq4h针脚电24v系统",
        ecu_name="电装D34(东风EQ4H)针脚电(24V系统",
        ecu_name_normalized="电装d34东风eq4h针脚电24v系统",
        system_voltage=24,
        pin_doc_kind="unknown",
        parsed_row_count=96,
        raw_content=None,
        last_synced_at=None,
    )
    index = ParameterKnowledgeIndex(
        built_at=datetime.utcnow(),
        sources_by_id={7: source_7, 77: source_77},
        rows_by_id={},
        rows_by_source={},
        rows_by_pin={},
        rows_by_ecu={},
        alias_lookup={
            "ecu": {
                "易控f02共轨": (
                    AliasEntry("ecu", "易控F02(共轨", "易控f02共轨", "易控F02(共轨", "易控f02共轨", 180, "generated", 7),
                ),
                "f02": (
                    AliasEntry("ecu", "易控F02(共轨", "易控f02共轨", "F02", "f02", 165, "generated", 7),
                ),
                "电装d34东风eq4h针脚电24v系统": (
                    AliasEntry(
                        "ecu",
                        "电装D34(东风EQ4H)针脚电(24V系统",
                        "电装d34东风eq4h针脚电24v系统",
                        "电装D34(东风EQ4H)针脚电(24V系统",
                        "电装d34东风eq4h针脚电24v系统",
                        180,
                        "generated",
                        77,
                    ),
                ),
            }
        },
    )
    service = ParameterQueryService(
        session_factory=lambda: None,
        external_repository=DummyExternalRepository(),
        index_store=ParameterQueryIndexStore(),
        llm_normalizer=FakeLLMNormalizer(),
    )

    matched_ids = service._find_explicit_source_ids(index, "易控F02 的 A-01 针脚定义是什么")

    assert matched_ids == [7]


def test_parameter_query_service_does_not_guess_source_when_llm_candidates_are_invalid():
    service = build_service_with_invalid_candidate_llm()

    result = service.query("这个 ECU 的 C244 引脚是什么作用")

    assert result["status"] == "need_clarify"
    assert result["clarify"]["question"] == "请先确认 ECU 型号"
    assert result["clarify"]["options"][0]["label"] == "大陆马牌SID208"


def test_parameter_query_service_recovers_source_candidates_from_component_target():
    service = build_service_with_invalid_candidate_llm()

    result = service.query("0H6风扇离合器电压多少")

    assert result["status"] == "need_clarify"
    assert result["clarify"]["question"] == "请先确认 ECU 型号"
    assert any("EDC17C53" in option["label"] for option in result["clarify"]["options"])


def test_parameter_query_service_supports_generic_voltage_bundle_value():
    service = build_service()
    row = service.index_store.get().rows_by_id[5]

    value = service._row_field_value(row, "voltage")
    summary = service._build_summary(
        service.index_store.get().sources_by_id[159],
        [row],
        "voltage",
    )

    assert value == "开路 12V / 静态 12V / 怠速 8V"
    assert "电压信息为 开路 12V / 静态 12V / 怠速 8V" in summary
