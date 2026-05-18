from __future__ import annotations

import json
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


@dataclass(frozen=True)
class AdapterCall:
    endpoint: str
    payload: dict[str, Any]
    headers: dict[str, str]
    files: list[tuple[str, str, bytes, str]] | None = None


@dataclass
class AdapterResult:
    endpoint: str
    request_payload: dict[str, Any]
    http_status: int | None = None
    raw_body: dict[str, Any] | None = None
    error_message: str | None = None


class DocSearchServiceAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        app_token: str | None,
        timeout_ms: int,
        top_k: int,
        request_mode: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_token = app_token
        self.timeout_ms = timeout_ms
        self.top_k = top_k
        self.request_mode = request_mode

    def build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.app_token:
            headers["x-app-token"] = self.app_token
        return headers

    def build_multipart_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.app_token:
            headers["x-app-token"] = self.app_token
        return headers

    def build_search_call(self, task) -> AdapterCall:
        return AdapterCall(
            endpoint=self.base_url + "/search",
            payload={
                "query": task.question_text,
                "filters": {},
                "limit": self.top_k,
            },
            headers=self.build_headers(),
        )

    def build_initial_chat_call(self, task) -> AdapterCall:
        message = task.initial_user_message or task.question_text
        if task.question_images:
            files: list[tuple[str, str, bytes, str]] = []
            for raw_path in task.question_images:
                path = Path(raw_path)
                content_type, _ = mimetypes.guess_type(str(path))
                files.append(
                    (
                        "images",
                        path.name,
                        path.read_bytes(),
                        content_type or "image/jpeg",
                    )
                )
            return AdapterCall(
                endpoint=self.base_url + "/chat/completions-with-images",
                payload={
                    "message": message,
                    "context": task.request_context,
                    "mode": self.request_mode,
                    "client_type": "benchmark",
                },
                headers=self.build_multipart_headers(),
                files=files,
            )
        return AdapterCall(
            endpoint=self.base_url + "/chat/completions",
            payload={
                "message": message,
                "context": task.request_context,
                "mode": self.request_mode,
            },
            headers=self.build_headers(),
        )

    def build_resume_chat_call(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        answer: str,
        selection_payload: dict[str, Any],
    ) -> AdapterCall:
        return AdapterCall(
            endpoint=self.base_url + "/chat/completions",
            payload={
                "session_id": session_id,
                "ask_user_answer": {
                    "tool_call_id": tool_call_id,
                    "answer": answer,
                    "metadata": {
                        "selection_payload": selection_payload,
                    },
                },
            },
            headers=self.build_headers(),
        )

    def build_call(self, task) -> AdapterCall:
        if task.benchmark_track == "search_api":
            return self.build_search_call(task)
        return self.build_initial_chat_call(task)

    @staticmethod
    def encode_multipart_formdata(
        *,
        payload: dict[str, Any],
        files: list[tuple[str, str, bytes, str]],
    ) -> tuple[bytes, str]:
        boundary = f"----CodexBenchmarkBoundary{uuid.uuid4().hex}"
        body = bytearray()

        request_json = json.dumps(payload, ensure_ascii=False)
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(b'Content-Disposition: form-data; name="request"\r\n')
        body.extend(b"Content-Type: application/json; charset=utf-8\r\n\r\n")
        body.extend(request_json.encode("utf-8"))
        body.extend(b"\r\n")

        for field_name, filename, content, content_type in files:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8")
            )
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
            body.extend(content)
            body.extend(b"\r\n")

        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        return bytes(body), boundary

    def execute(self, call: AdapterCall) -> AdapterResult:
        result = AdapterResult(endpoint=call.endpoint, request_payload=call.payload)
        headers = dict(call.headers)
        if call.files:
            body, boundary = self.encode_multipart_formdata(payload=call.payload, files=call.files)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        else:
            body = json.dumps(call.payload, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(call.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib_request.urlopen(request, timeout=self.timeout_ms / 1000.0) as response:
                result.http_status = getattr(response, "status", 200)
                payload = response.read().decode("utf-8")
                result.raw_body = json.loads(payload) if payload else {}
        except urllib_error.HTTPError as exc:
            result.http_status = int(exc.code)
            payload = exc.read().decode("utf-8", errors="replace")
            try:
                result.raw_body = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                result.raw_body = {"raw_body": payload}
            result.error_message = f"HTTP {exc.code}"
        except Exception as exc:  # pragma: no cover - depends on runtime/network
            result.error_message = str(exc)
        return result
