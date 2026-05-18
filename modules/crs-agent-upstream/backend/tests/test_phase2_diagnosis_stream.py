import asyncio
import json

from fastapi.testclient import TestClient
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.adapters.legacy_fault_diag_adapter import LegacyFaultDiagAdapter
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.service import ActiveStreamState, AgentLoopService
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import Settings
from app.legacy.services.diagnosis import (
    BatchEcusResult,
    BatchReportItem,
    BatchReportsResult,
    DiagnosisResult,
    EcusByFaultCodeResult,
    EcuSummaryItem,
    ImageRecognitionResult,
    RecognizedFaultCode,
    get_fault_code_parser,
)
from app.main import create_app


class FakeConfigService:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)


class FakeDiagnosisClient:
    async def get_ecus_by_fault_code(self, fault_code: str) -> EcusByFaultCodeResult:
        if fault_code == "P01F5":
            return EcusByFaultCodeResult(
                success=True,
                fault_code=fault_code,
                ecu_models=["EDC17CV44", "MD1CS004"],
                count=2,
                message="multiple",
            )
        return EcusByFaultCodeResult(
            success=True,
            fault_code=fault_code,
            ecu_models=["EDC17CV44"],
            count=1,
            message="single",
        )

    async def ensure_latest(
        self,
        ecu_model: str,
        fault_code: str,
        no_back: bool = False,
        show_back: bool = True,
        return_url: str | None = None,
    ) -> DiagnosisResult:
        return DiagnosisResult(
            success=True,
            state="ready",
            fault_code=fault_code,
            ecu_model=ecu_model,
            report_url=f"https://diag.example/{fault_code}/{ecu_model}",
            task_id=None,
            subscribe_url=None,
            report_id=1001,
        )

    async def recognize_image(self, image_content: bytes, filename: str) -> ImageRecognitionResult:
        return ImageRecognitionResult(
            success=True,
            fault_codes=[
                RecognizedFaultCode(
                    raw="P01F5",
                    normalized="P01F5",
                    code_type="OBD_PCODE",
                    description="后处理相关故障",
                    status="当前",
                )
            ],
            count=1,
        )

    async def get_batch_ecus(self, fault_codes: list[str]) -> BatchEcusResult:
        return BatchEcusResult(
            success=True,
            ecu_summary=[
                EcuSummaryItem(
                    ecu_model="EDC17CV44",
                    match_count=len(fault_codes),
                    matched_codes=fault_codes,
                    recommended=True,
                )
            ],
            code_details={code: ["EDC17CV44"] for code in fault_codes},
        )

    async def get_batch_reports(
        self,
        fault_codes: list[str],
        ecu_model: str,
        return_url: str | None = None,
    ) -> BatchReportsResult:
        return BatchReportsResult(
            success=True,
            ecu_model=ecu_model,
            reports=[
                BatchReportItem(
                    fault_code=code,
                    state="ready",
                    report_url=f"https://diag.example/{code}/{ecu_model}",
                    task_id=None,
                    subscribe_url=None,
                    report_id=idx + 1,
                )
                for idx, code in enumerate(fault_codes)
            ],
        )


def build_test_deps(tmp_path) -> AgentRuntimeDeps:
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        config_service=FakeConfigService({"diagnosis_service_enabled": True}),
        diagnosis_client=FakeDiagnosisClient(),
        fault_code_parser=get_fault_code_parser(),
    )


def test_fault_diag_adapter_returns_need_clarify_for_multiple_ecus(tmp_path):
    deps = build_test_deps(tmp_path)
    adapter = LegacyFaultDiagAdapter(deps)

    result = asyncio.run(adapter.lookup_ecu_candidates("P01F5 故障码"))

    assert result["status"] == "need_clarify"
    assert result["data"]["fault_code"] == "P01F5"
    assert result["clarify"]["question"] == "识别到故障码 P01F5，请选择对应 ECU："
    assert [item["label"] for item in result["clarify"]["options"]] == ["EDC17CV44", "MD1CS004"]
    assert result["clarify"]["context"]["fault_code"] == "P01F5"
    assert result["clarify"]["options"][0]["selection_payload"]["filters"]["fault_code"] == "P01F5"
    assert result["clarify"]["options"][0]["selection_payload"]["filters"]["ecu_model"] == "EDC17CV44"


def test_fault_diag_adapter_diagnose_returns_ready_payload(tmp_path):
    deps = build_test_deps(tmp_path)
    adapter = LegacyFaultDiagAdapter(deps)

    result = asyncio.run(adapter.diagnose("P01F5", "EDC17CV44"))

    assert result["status"] == "ok"
    assert result["data"]["state"] == "ready"
    assert result["data"]["fault_code"] == "P01F5"
    assert result["data"]["ecu_model"] == "EDC17CV44"
    assert result["data"]["report_url"] == "https://diag.example/P01F5/EDC17CV44"


def test_phase2_diagnosis_api_compat_routes(tmp_path):
    deps = build_test_deps(tmp_path)
    app = create_app()

    with TestClient(app) as client:
        app.state.runtime_deps = deps
        app.state.agent_service = AgentLoopService(
            deps=deps,
            factory=AgentFactory(settings=Settings(agent_model="test", agent_test_output_text="ok")),
        )

        available = client.get("/chat/api/image/diagnosis-available")
        assert available.status_code == 200
        assert available.json() == {"available": True}

        batch_ecus = client.post("/chat/api/diagnosis/batch-ecus", json={"fault_codes": ["P01F5", "P0101"]})
        assert batch_ecus.status_code == 200
        assert batch_ecus.json()["ecu_summary"][0]["ecu_model"] == "EDC17CV44"

        batch_reports = client.post(
            "/chat/api/diagnosis/batch-reports",
            json={"fault_codes": ["P01F5"], "ecu_model": "EDC17CV44"},
        )
        assert batch_reports.status_code == 200
        assert batch_reports.json()["reports"][0]["report_url"] == "https://diag.example/P01F5/EDC17CV44"

        recognize = client.post(
            "/chat/api/image/recognize-fault-codes",
            files={"image": ("fault.jpg", b"fake-image", "image/jpeg")},
        )
        assert recognize.status_code == 200
        assert recognize.json()["fault_codes"][0]["normalized"] == "P01F5"


def test_phase2_stream_endpoint_and_abort(tmp_path):
    deps = build_test_deps(tmp_path)

    async def stream_llm(_messages: list, _info: AgentInfo):
        yield "stream-"
        yield "ok"

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(stream_function=stream_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    app = create_app()
    with TestClient(app) as client:
        app.state.runtime_deps = deps
        app.state.agent_service = service

        response = client.post("/chat/api/chat/stream", json={"message": "hello"})
        assert response.status_code == 200

        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[0]["type"] == "start"
        assert events[1]["type"] == "hint"
        done_event = next(event for event in events if event["type"] == "done")
        assert done_event["full_content"] == "stream-ok"
        assert done_event["response"]["type"] == "message"

        service._active_streams["abort_sess"] = ActiveStreamState(message_history=None, user_prompt="继续输出")
        aborted = client.post(
            "/chat/api/chat/stream/abort",
            json={"session_id": "abort_sess", "partial_content": "部分内容"},
        )
        assert aborted.status_code == 200
        assert aborted.json()["status"] == "ok"

        serialized_history = deps.message_history_store.load_serialized_history("abort_sess")
        assert serialized_history is not None
        assert "继续输出" in serialized_history
        assert "部分内容" in serialized_history
