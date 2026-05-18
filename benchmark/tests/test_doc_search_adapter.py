from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.envs.doc_search.adapters import DocSearchServiceAdapter
from doc_search_bench.envs.doc_search.preprocessors import prepare_request_context
from doc_search_bench.types import TaskCase


def build_task(*, question_images: list[str], request_context: dict | None = None, preprocess_strategy: str = "ocr") -> TaskCase:
    return TaskCase(
        case_id="case_image_001",
        split="dev",
        layer="component",
        suite_id="suite_demo",
        input_modality="image_text" if question_images else "text",
        question_text="帮我找这个仪表资料",
        question_images=question_images,
        vehicle_info=None,
        preprocess_strategy=preprocess_strategy,
        benchmark_track="chat_completions",
        request_context=request_context or {},
        accepted_titles=[],
        preferred_title=None,
        user_id="benchmark_user",
        instruction="",
    )


def test_build_initial_chat_call_without_images_uses_json_endpoint():
    adapter = DocSearchServiceAdapter(
        base_url="http://127.0.0.1:8000",
        app_token="token",
        timeout_ms=30_000,
        top_k=10,
        request_mode="doc_search",
    )
    task = build_task(question_images=[], preprocess_strategy="none")

    call = adapter.build_initial_chat_call(task)

    assert call.endpoint == "http://127.0.0.1:8000/chat/completions"
    assert call.files is None
    assert call.payload["message"] == task.question_text
    assert call.payload["mode"] == "doc_search"
    assert call.headers["Content-Type"] == "application/json"


def test_build_initial_chat_call_with_images_uses_multipart_endpoint(tmp_path: Path):
    image_path = tmp_path / "dash.jpg"
    image_bytes = b"fake-image-bytes"
    image_path.write_bytes(image_bytes)

    adapter = DocSearchServiceAdapter(
        base_url="http://127.0.0.1:8000",
        app_token="token",
        timeout_ms=30_000,
        top_k=10,
        request_mode="doc_search",
    )
    task = build_task(question_images=[str(image_path)], request_context={"trace_id": "demo"})

    call = adapter.build_initial_chat_call(task)

    assert call.endpoint == "http://127.0.0.1:8000/chat/completions-with-images"
    assert call.files is not None
    assert len(call.files) == 1
    field_name, filename, content, content_type = call.files[0]
    assert field_name == "images"
    assert filename == "dash.jpg"
    assert content == image_bytes
    assert content_type == "image/jpeg"
    assert call.payload["client_type"] == "benchmark"
    assert call.payload["context"] == {"trace_id": "demo"}
    assert "Content-Type" not in call.headers


def test_encode_multipart_formdata_contains_request_and_images():
    body, boundary = DocSearchServiceAdapter.encode_multipart_formdata(
        payload={"message": "hello", "mode": "doc_search"},
        files=[("images", "dash.jpg", b"abc123", "image/jpeg")],
    )
    text = body.decode("utf-8", errors="ignore")

    assert boundary
    assert 'name="request"' in text
    assert '"message": "hello"' in text
    assert 'name="images"; filename="dash.jpg"' in text
    assert "Content-Type: image/jpeg" in text


def test_prepare_request_context_accepts_image_cases_without_prefilled_context(tmp_path: Path):
    image_path = tmp_path / "ecu.png"
    image_path.write_bytes(b"img")
    task = build_task(question_images=[str(image_path)], request_context={}, preprocess_strategy="ocr")

    outcome = prepare_request_context(task)

    assert outcome.used_image_context is True
    assert outcome.blocking_failures == []
    assert outcome.request_context == {}


def test_prepare_request_context_still_blocks_text_case_without_context():
    task = build_task(question_images=[], request_context={}, preprocess_strategy="ocr")

    outcome = prepare_request_context(task)

    assert outcome.used_image_context is False
    assert outcome.blocking_failures == ["OCR_CONTEXT_MISSING"]
