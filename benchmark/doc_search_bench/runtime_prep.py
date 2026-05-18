from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from .types import TaskSuite


DEFAULT_REDIS_HOST = "127.0.0.1"
DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_WAIT_SECONDS = 8.0
DEFAULT_DOC_SEARCH_WARMUP_TIMEOUT_MS = 240000
DEFAULT_DOC_SEARCH_WARMUP_MESSAGE = "请帮我找一份整车电路图。"
FAST_SMOKE_SCENARIOS = {"normal"}
FAST_SMOKE_SPLITS = {"train", "dev"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _socket_connectable(host: str, port: int, timeout_seconds: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_seconds)
        try:
            sock.connect((host, port))
        except OSError:
            return False
        return True


def redis_is_ready(host: str = DEFAULT_REDIS_HOST, port: int = DEFAULT_REDIS_PORT) -> bool:
    return _socket_connectable(host, port)


def _candidate_redis_commands() -> list[list[str]]:
    mini_server = repo_root() / "benchmark" / "scripts" / "mini_redis_server.py"
    return [
        [sys.executable, str(mini_server), "--host", DEFAULT_REDIS_HOST, "--port", str(DEFAULT_REDIS_PORT)],
        ["redis-server"],
        ["wsl.exe", "-e", "redis-server", "--port", str(DEFAULT_REDIS_PORT)],
        [
            "wsl.exe",
            "-e",
            "sh",
            "-lc",
            f"redis-server --port {DEFAULT_REDIS_PORT} --daemonize yes",
        ],
        [
            "wsl.exe",
            "-e",
            "sh",
            "-lc",
            f"nohup redis-server --port {DEFAULT_REDIS_PORT} >/tmp/crs-benchmark-redis.log 2>&1 &",
        ],
    ]


def ensure_local_redis_running() -> dict[str, Any]:
    host = os.environ.get("BENCHMARK_REDIS_HOST", DEFAULT_REDIS_HOST).strip() or DEFAULT_REDIS_HOST
    port_raw = os.environ.get("BENCHMARK_REDIS_PORT", str(DEFAULT_REDIS_PORT))
    wait_seconds_raw = os.environ.get("BENCHMARK_REDIS_WAIT_SECONDS", str(DEFAULT_REDIS_WAIT_SECONDS))
    try:
        port = int(port_raw)
    except ValueError:
        port = DEFAULT_REDIS_PORT
    try:
        wait_seconds = float(wait_seconds_raw)
    except ValueError:
        wait_seconds = DEFAULT_REDIS_WAIT_SECONDS

    if redis_is_ready(host=host, port=port):
        return {
            "attempted": False,
            "ready": True,
            "host": host,
            "port": port,
            "method": "already_running",
        }

    errors: list[str] = []
    for command in _candidate_redis_commands():
        try:
            subprocess.Popen(
                command,
                cwd=str(repo_root()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            errors.append(f"{' '.join(command)} => {exc}")
            continue

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if redis_is_ready(host=host, port=port):
                return {
                    "attempted": True,
                    "ready": True,
                    "host": host,
                    "port": port,
                    "method": " ".join(command),
                }
            time.sleep(0.2)

        errors.append(f"{' '.join(command)} => redis_not_ready_after_wait")

    return {
        "attempted": True,
        "ready": False,
        "host": host,
        "port": port,
        "errors": errors,
    }


def _build_headers(app_token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if app_token:
        headers["x-app-token"] = app_token
    return headers


def warmup_doc_search(
    *,
    base_url: str,
    app_token: str | None,
    timeout_ms: int,
    message: str = DEFAULT_DOC_SEARCH_WARMUP_MESSAGE,
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "message": message,
        "context": {},
        "mode": "doc_search",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib_request.Request(
        endpoint,
        data=body,
        headers=_build_headers(app_token),
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib_request.urlopen(request, timeout=timeout_ms / 1000.0) as response:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            raw_body = response.read().decode("utf-8")
            parsed = json.loads(raw_body) if raw_body else {}
            return {
                "ok": True,
                "endpoint": endpoint,
                "elapsed_ms": elapsed_ms,
                "http_status": getattr(response, "status", 200),
                "response_type": parsed.get("type"),
                "business": parsed.get("business"),
            }
    except urllib_error.HTTPError as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        payload_text = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "endpoint": endpoint,
            "elapsed_ms": elapsed_ms,
            "http_status": int(exc.code),
            "error": f"HTTP {exc.code}",
            "response_text": payload_text[:800],
        }
    except Exception as exc:  # pragma: no cover - depends on runtime/network
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return {
            "ok": False,
            "endpoint": endpoint,
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
        }


def select_fast_smoke_suites(suites: list[TaskSuite], *, split: str) -> list[TaskSuite]:
    if split not in FAST_SMOKE_SPLITS:
        return suites

    filtered: list[TaskSuite] = []
    for suite in suites:
        fast_cases = [
            case
            for case in suite.cases
            if case.user_simulation_config.scenario in FAST_SMOKE_SCENARIOS
        ]
        if not fast_cases:
            continue
        filtered.append(
            TaskSuite(
                split=suite.split,
                suite_id=suite.suite_id,
                layer=suite.layer,
                acceptance_threshold=suite.acceptance_threshold,
                source_files=list(suite.source_files),
                cases=fast_cases,
                legacy_source_split=suite.legacy_source_split,
            )
        )
    return filtered or suites
