from __future__ import annotations

import argparse
import configparser
import json
import mimetypes
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib import parse as urllib_parse


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


from doc_search_bench.run import TASK_SUITES_BY_SPLIT, filter_suites, main as benchmark_main
from doc_search_bench.runtime_prep import ensure_local_redis_running
from doc_search_bench.user import _resolve_completion_target
from doc_search_bench.user_model_defaults import load_backend_env, resolve_user_model_defaults

_INCOMPLETE_FINAL_STATUSES = {
    "error_http",
    "stopped_invalid_user_decision",
    "stopped_missing_selection_payload",
    "stopped_missing_session_id",
    "stopped_missing_tool_call_id",
    "stopped_rollback_unsupported",
}
DEFAULT_PROXY_URL = "http://127.0.0.1:7897"
DEFAULT_SAKURACAT_EXE = r"C:\Vpn\SakuraCat\SakuraCat.exe"
DEFAULT_BENCHMARK_USER_TIMEOUT_SECONDS = 600.0
DEFAULT_OPENROUTER_RETRY_ATTEMPTS = 4
DEFAULT_OPENROUTER_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_OPENROUTER_PREFLIGHT_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_OPENROUTER_PREFLIGHT_RETRIES = 3
DEFAULT_OPENROUTER_PREFLIGHT_BACKOFF_SECONDS = 2.0
DEFAULT_IMAGE_REQUEST_RETRY_ATTEMPTS = 3
DEFAULT_IMAGE_REQUEST_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_MYSQL_HOST = "127.0.0.1"
DEFAULT_MYSQL_PORT = 3306
DEFAULT_MYSQL_WAIT_SECONDS = 15.0
ROUND_REVIEW_HTML_FILENAME = "round_case_review.html"
DEFAULT_IMAGE_PROBE_QUESTION = "请先根据当前图片做一次简短识别；如果信息不足，请直接发起一个澄清问题。"
_RETRYABLE_TRANSPORT_ERROR_MARKERS = (
    "unexpected_eof_while_reading",
    "eof occurred in violation of protocol",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "connection dropped",
    "remote end closed connection",
    "handshake",
    "ssl",
    "tls",
)


def _repo_root() -> Path:
    return CURRENT_DIR.parent


def _read_token(token_file: Path) -> str:
    return token_file.read_text(encoding="utf-8").strip()


def _token_file_status(token_file: Path) -> dict[str, Any]:
    if not token_file.exists():
        return {"ok": False, "reason": "token_file_missing", "token_file": str(token_file)}
    try:
        token = _read_token(token_file)
    except Exception as exc:
        return {"ok": False, "reason": "token_file_unreadable", "token_file": str(token_file), "detail": str(exc)}
    if not token:
        return {"ok": False, "reason": "token_file_empty", "token_file": str(token_file)}
    return {"ok": True, "token_file": str(token_file), "token_length": len(token)}


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _make_one_click_run_id() -> str:
    return time.strftime("one-click-%Y%m%dT%H%M%SZ", time.gmtime())


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _resolve_backend_model_defaults() -> dict[str, str]:
    backend_env = load_backend_env()
    fallback_agent_model = "openrouter:deepseek/deepseek-chat-v3-0324"

    agent_model = str(
        backend_env.get("CRS_AGENT_MODEL")
        or backend_env.get("AGENT_MODEL")
        or fallback_agent_model
    ).strip() or fallback_agent_model
    clarify_model = str(
        backend_env.get("CRS_OPENROUTER_CLARIFY_MODEL")
        or backend_env.get("OPENROUTER_CLARIFY_MODEL")
        or agent_model
    ).strip() or agent_model
    intent_model = str(
        backend_env.get("CRS_INTENT_ROUTER_MODEL")
        or backend_env.get("INTENT_ROUTER_MODEL")
        or agent_model
    ).strip() or agent_model
    coding_model = str(
        backend_env.get("CRS_CODING_ENGINE_MODEL")
        or backend_env.get("CODING_ENGINE_MODEL")
        or agent_model
    ).strip() or agent_model

    return {
        "agent_model": agent_model,
        "openrouter_clarify_model": clarify_model,
        "intent_router_model": intent_model,
        "coding_engine_model": coding_model,
    }


def _backend_openrouter_env_status() -> dict[str, Any]:
    backend_env = load_backend_env()
    api_key = str(
        backend_env.get("OPENROUTER_API_KEY")
        or backend_env.get("CRS_OPENROUTER_API_KEY")
        or ""
    ).strip()
    base_url = str(
        backend_env.get("OPENROUTER_BASE_URL")
        or backend_env.get("CRS_OPENROUTER_BASE_URL")
        or DEFAULT_OPENROUTER_PREFLIGHT_URL.rsplit("/", 1)[0]
    ).strip()
    if not api_key:
        return {"ok": False, "reason": "openrouter_api_key_missing"}
    if not base_url:
        return {"ok": False, "reason": "openrouter_base_url_missing"}
    return {"ok": True, "openrouter_base_url": base_url, "openrouter_api_key": "***"}


def _is_port_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=2.0):
            return True
    except Exception:
        return False


def _start_sakuracat_vpn(exe_path: Path) -> dict[str, Any]:
    if not exe_path.exists():
        return {"ok": False, "reason": "vpn_exe_missing", "exe_path": str(exe_path)}
    subprocess.Popen(
        [str(exe_path)],
        cwd=str(exe_path.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    return {"ok": True, "exe_path": str(exe_path), "started": True}


def _ensure_proxy_ready(*, proxy_url: str, vpn_exe_path: Path, timeout_seconds: float = 120.0) -> dict[str, Any]:
    normalized_proxy_url = str(proxy_url or "").strip()
    parsed = urllib_parse.urlparse(normalized_proxy_url)
    host = str(parsed.hostname or "127.0.0.1").strip()
    port = parsed.port or 7897

    if _is_port_listening(host, port):
        return {"ok": True, "proxy_url": normalized_proxy_url, "host": host, "port": port, "started_vpn": False}

    start_result = _start_sakuracat_vpn(vpn_exe_path)
    if not bool(start_result.get("ok")):
        return {
            "ok": False,
            "proxy_url": normalized_proxy_url,
            "host": host,
            "port": port,
            "started_vpn": False,
            "vpn_start": start_result,
        }

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _is_port_listening(host, port):
            return {
                "ok": True,
                "proxy_url": normalized_proxy_url,
                "host": host,
                "port": port,
                "started_vpn": True,
                "vpn_start": start_result,
            }
        time.sleep(2.0)

    return {
        "ok": False,
        "proxy_url": normalized_proxy_url,
        "host": host,
        "port": port,
        "started_vpn": True,
        "vpn_start": start_result,
        "reason": "vpn_started_but_proxy_not_ready",
    }


def _repo_mysql_root(*, repo_root: Path | None = None) -> Path:
    resolved_repo_root = (repo_root or _repo_root()).resolve()
    return resolved_repo_root / ".local" / "mysql"


def _read_repo_mysql_config(*, repo_root: Path | None = None) -> dict[str, Any]:
    mysql_root = _repo_mysql_root(repo_root=repo_root)
    defaults_file = mysql_root / "my.ini"
    if not defaults_file.exists():
        return {
            "ok": False,
            "reason": "mysql_defaults_file_missing",
            "mysql_root": str(mysql_root),
            "defaults_file": str(defaults_file),
        }

    parser = configparser.ConfigParser()
    try:
        parser.read(defaults_file, encoding="utf-8")
    except Exception as exc:
        return {
            "ok": False,
            "reason": "mysql_defaults_file_unreadable",
            "mysql_root": str(mysql_root),
            "defaults_file": str(defaults_file),
            "detail": str(exc),
        }

    if not parser.has_section("mysqld"):
        return {
            "ok": False,
            "reason": "mysql_defaults_missing_mysqld_section",
            "mysql_root": str(mysql_root),
            "defaults_file": str(defaults_file),
        }

    host = str(parser.get("mysqld", "bind-address", fallback=DEFAULT_MYSQL_HOST) or DEFAULT_MYSQL_HOST).strip()
    try:
        port = int(parser.get("mysqld", "port", fallback=str(DEFAULT_MYSQL_PORT)))
    except ValueError:
        port = DEFAULT_MYSQL_PORT
    basedir_raw = str(parser.get("mysqld", "basedir", fallback="") or "").strip()
    basedir = Path(basedir_raw.replace("\\", "/")).resolve() if basedir_raw else None
    client_user = str(parser.get("client", "user", fallback="") or "").strip() or None
    client_password = str(parser.get("client", "password", fallback="") or "").strip() or None
    client_host = str(parser.get("client", "host", fallback="") or "").strip() or None
    try:
        client_port = int(parser.get("client", "port", fallback="0") or 0) or None
    except ValueError:
        client_port = None
    run_dir = mysql_root / "run"
    stdout_log_path = run_dir / "mysqld.stdout.log"
    stderr_log_path = run_dir / "mysqld.stderr.log"
    mysqld_path = (basedir / "bin" / "mysqld.exe").resolve() if basedir is not None else None

    return {
        "ok": True,
        "mysql_root": str(mysql_root),
        "defaults_file": str(defaults_file),
        "host": host or DEFAULT_MYSQL_HOST,
        "port": port,
        "basedir": str(basedir) if basedir is not None else None,
        "mysqld_path": str(mysqld_path) if mysqld_path is not None else None,
        "client_user": client_user,
        "client_password": client_password,
        "client_host": client_host,
        "client_port": client_port,
        "run_dir": str(run_dir),
        "stdout_log_path": str(stdout_log_path),
        "stderr_log_path": str(stderr_log_path),
    }


def _start_repo_mysql_process(mysql_config: dict[str, Any]) -> dict[str, Any]:
    mysqld_path_raw = mysql_config.get("mysqld_path")
    defaults_file_raw = mysql_config.get("defaults_file")
    mysql_root_raw = mysql_config.get("mysql_root")
    stdout_log_path_raw = mysql_config.get("stdout_log_path")
    stderr_log_path_raw = mysql_config.get("stderr_log_path")

    if not isinstance(mysqld_path_raw, str) or not mysqld_path_raw.strip():
        return {"ok": False, "reason": "mysqld_path_missing"}
    if not isinstance(defaults_file_raw, str) or not defaults_file_raw.strip():
        return {"ok": False, "reason": "mysql_defaults_file_missing"}
    if not isinstance(mysql_root_raw, str) or not mysql_root_raw.strip():
        return {"ok": False, "reason": "mysql_root_missing"}
    if not isinstance(stdout_log_path_raw, str) or not stdout_log_path_raw.strip():
        return {"ok": False, "reason": "mysql_stdout_log_path_missing"}
    if not isinstance(stderr_log_path_raw, str) or not stderr_log_path_raw.strip():
        return {"ok": False, "reason": "mysql_stderr_log_path_missing"}

    mysqld_path = Path(mysqld_path_raw)
    defaults_file = Path(defaults_file_raw)
    mysql_root = Path(mysql_root_raw)
    stdout_log_path = Path(stdout_log_path_raw)
    stderr_log_path = Path(stderr_log_path_raw)

    if not mysqld_path.exists():
        return {"ok": False, "reason": "mysqld_exe_missing", "mysqld_path": str(mysqld_path)}
    if not defaults_file.exists():
        return {"ok": False, "reason": "mysql_defaults_file_missing", "defaults_file": str(defaults_file)}

    stdout_log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_log_path.touch(exist_ok=True)
    stderr_log_path.touch(exist_ok=True)

    with open(stdout_log_path, "ab") as stdout_handle, open(stderr_log_path, "ab") as stderr_handle:
        try:
            proc = subprocess.Popen(
                [str(mysqld_path), f"--defaults-file={defaults_file}"],
                cwd=str(mysql_root),
                stdout=stdout_handle,
                stderr=stderr_handle,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            return {
                "ok": False,
                "reason": "mysqld_start_failed",
                "mysqld_path": str(mysqld_path),
                "defaults_file": str(defaults_file),
                "detail": str(exc),
            }

    return {
        "ok": True,
        "pid": int(proc.pid),
        "method": f"{mysqld_path.name} --defaults-file={defaults_file}",
        "mysqld_path": str(mysqld_path),
        "defaults_file": str(defaults_file),
        "stdout_log_path": str(stdout_log_path),
        "stderr_log_path": str(stderr_log_path),
    }


def _wait_for_port_ready(host: str, port: int, *, timeout_seconds: float, poll_seconds: float = 0.2) -> bool:
    deadline = time.time() + max(0.0, float(timeout_seconds))
    while time.time() < deadline:
        if _is_port_listening(host, port):
            return True
        time.sleep(max(0.05, float(poll_seconds)))
    return _is_port_listening(host, port)


def _ensure_local_mysql_running(*, repo_root: Path | None = None, wait_seconds: float | None = None) -> dict[str, Any]:
    host = DEFAULT_MYSQL_HOST
    port = DEFAULT_MYSQL_PORT
    mysql_root = _repo_mysql_root(repo_root=repo_root)
    defaults_file = mysql_root / "my.ini"

    if _is_port_listening(host, port):
        return {
            "attempted": False,
            "ready": True,
            "host": host,
            "port": port,
            "method": "already_running",
            "mysql_root": str(mysql_root),
            "defaults_file": str(defaults_file),
        }

    mysql_config = _read_repo_mysql_config(repo_root=repo_root)
    if not bool(mysql_config.get("ok")):
        return {
            "attempted": False,
            "ready": False,
            **mysql_config,
        }

    host = str(mysql_config.get("host") or host)
    port = int(mysql_config.get("port") or port)
    timeout_seconds = DEFAULT_MYSQL_WAIT_SECONDS if wait_seconds is None else max(0.0, float(wait_seconds))

    if _is_port_listening(host, port):
        return {
            "attempted": False,
            "ready": True,
            "host": host,
            "port": port,
            "method": "already_running",
            **mysql_config,
        }

    start_result = _start_repo_mysql_process(mysql_config)
    if not bool(start_result.get("ok")):
        return {
            "attempted": True,
            "ready": False,
            "host": host,
            "port": port,
            "errors": [str(start_result.get("reason") or "mysqld_start_failed")],
            **mysql_config,
            "start_result": start_result,
        }

    if _wait_for_port_ready(host, port, timeout_seconds=timeout_seconds):
        return {
            "attempted": True,
            "ready": True,
            "host": host,
            "port": port,
            "method": str(start_result.get("method") or "mysqld"),
            **mysql_config,
            "start_result": start_result,
        }

    return {
        "attempted": True,
        "ready": False,
        "host": host,
        "port": port,
        "errors": ["mysqld_started_but_port_not_ready"],
        **mysql_config,
        "start_result": start_result,
    }


def _proxy_env_overrides(proxy_url: str) -> dict[str, str]:
    normalized_proxy_url = str(proxy_url or "").strip()
    if not normalized_proxy_url:
        return {}
    return {
        "http_proxy": normalized_proxy_url,
        "https_proxy": normalized_proxy_url,
        "HTTP_PROXY": normalized_proxy_url,
        "HTTPS_PROXY": normalized_proxy_url,
        "all_proxy": normalized_proxy_url,
        "ALL_PROXY": normalized_proxy_url,
        "NO_PROXY": "wx.51gonggui.com,127.0.0.1,localhost",
        "no_proxy": "wx.51gonggui.com,127.0.0.1,localhost",
    }


def _user_stability_env_overrides() -> dict[str, str]:
    return {
        "BENCHMARK_USER_OPENAI_COMPAT_FRESH_CLIENT": "1",
        "BENCHMARK_USER_TIMEOUT_SECONDS": str(
            os.environ.get("BENCHMARK_USER_TIMEOUT_SECONDS") or DEFAULT_BENCHMARK_USER_TIMEOUT_SECONDS
        ),
        "BENCHMARK_OPENROUTER_TIMEOUT_SECONDS": str(
            os.environ.get("BENCHMARK_OPENROUTER_TIMEOUT_SECONDS") or DEFAULT_BENCHMARK_USER_TIMEOUT_SECONDS
        ),
        "BENCHMARK_OPENROUTER_RETRY_ATTEMPTS": str(
            os.environ.get("BENCHMARK_OPENROUTER_RETRY_ATTEMPTS") or DEFAULT_OPENROUTER_RETRY_ATTEMPTS
        ),
        "BENCHMARK_OPENROUTER_RETRY_BACKOFF_SECONDS": str(
            os.environ.get("BENCHMARK_OPENROUTER_RETRY_BACKOFF_SECONDS")
            or DEFAULT_OPENROUTER_RETRY_BACKOFF_SECONDS
        ),
        "BENCHMARK_OPENROUTER_PREFLIGHT_URL": str(
            os.environ.get("BENCHMARK_OPENROUTER_PREFLIGHT_URL") or DEFAULT_OPENROUTER_PREFLIGHT_URL
        ),
        "BENCHMARK_OPENROUTER_PREFLIGHT_RETRIES": str(
            os.environ.get("BENCHMARK_OPENROUTER_PREFLIGHT_RETRIES") or DEFAULT_OPENROUTER_PREFLIGHT_RETRIES
        ),
        "BENCHMARK_OPENROUTER_PREFLIGHT_BACKOFF_SECONDS": str(
            os.environ.get("BENCHMARK_OPENROUTER_PREFLIGHT_BACKOFF_SECONDS")
            or DEFAULT_OPENROUTER_PREFLIGHT_BACKOFF_SECONDS
        ),
        "BENCHMARK_IMAGE_REQUEST_RETRY_ATTEMPTS": str(
            os.environ.get("BENCHMARK_IMAGE_REQUEST_RETRY_ATTEMPTS")
            or DEFAULT_IMAGE_REQUEST_RETRY_ATTEMPTS
        ),
        "BENCHMARK_IMAGE_REQUEST_RETRY_BACKOFF_SECONDS": str(
            os.environ.get("BENCHMARK_IMAGE_REQUEST_RETRY_BACKOFF_SECONDS")
            or DEFAULT_IMAGE_REQUEST_RETRY_BACKOFF_SECONDS
        ),
    }


def _load_backend_mysql_defaults(*, repo_root: Path | None = None) -> dict[str, str]:
    backend_env = load_backend_env()
    values = {
        "CRS_MYSQL_HOST": str(backend_env.get("CRS_MYSQL_HOST") or "").strip(),
        "CRS_MYSQL_PORT": str(backend_env.get("CRS_MYSQL_PORT") or "").strip(),
        "CRS_MYSQL_USER": str(backend_env.get("CRS_MYSQL_USER") or "").strip(),
        "CRS_MYSQL_PASSWORD": str(backend_env.get("CRS_MYSQL_PASSWORD") or "").strip(),
        "CRS_MYSQL_DATABASE": str(backend_env.get("CRS_MYSQL_DATABASE") or "").strip(),
    }
    if all(values.values()):
        return values

    resolved_repo_root = (repo_root or _repo_root()).resolve()
    backend_example = resolved_repo_root / "modules" / "crs-agent-upstream" / "backend" / ".env.example"
    if not backend_example.exists():
        return {key: value for key, value in values.items() if value}

    parser_values: dict[str, str] = {}
    for raw_line in backend_example.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        env_key = key.strip()
        env_value = raw_value.strip()
        if not env_key or env_key not in values or not env_value:
            continue
        if len(env_value) >= 2 and env_value[0] == env_value[-1] and env_value[0] in {"'", '"'}:
            env_value = env_value[1:-1]
        parser_values.setdefault(env_key, env_value)

    merged = dict(values)
    for key, value in parser_values.items():
        if not merged.get(key):
            merged[key] = value
    return {key: value for key, value in merged.items() if value}


def _repo_mysql_env_overrides(mysql_config: dict[str, Any] | None, *, repo_root: Path | None = None) -> dict[str, str]:
    if not isinstance(mysql_config, dict):
        mysql_config = {}
    backend_defaults = _load_backend_mysql_defaults(repo_root=repo_root)
    user = str(mysql_config.get("client_user") or backend_defaults.get("CRS_MYSQL_USER") or "").strip()
    password = str(mysql_config.get("client_password") or backend_defaults.get("CRS_MYSQL_PASSWORD") or "").strip()
    host = str(
        mysql_config.get("client_host")
        or backend_defaults.get("CRS_MYSQL_HOST")
        or mysql_config.get("host")
        or ""
    ).strip()
    port = (
        mysql_config.get("client_port")
        or backend_defaults.get("CRS_MYSQL_PORT")
        or mysql_config.get("port")
    )
    database = str(backend_defaults.get("CRS_MYSQL_DATABASE") or "").strip()
    overrides: dict[str, str] = {}
    if user:
        overrides["CRS_MYSQL_USER"] = user
    if password:
        overrides["CRS_MYSQL_PASSWORD"] = password
    if host:
        overrides["CRS_MYSQL_HOST"] = host
    if port not in (None, ""):
        overrides["CRS_MYSQL_PORT"] = str(port)
    if database:
        overrides["CRS_MYSQL_DATABASE"] = database
    return overrides


def _apply_env_overrides_temporarily(overrides: dict[str, str]):
    previous = {key: os.environ.get(key) for key in overrides}

    class _EnvOverrideContext:
        def __enter__(self):
            for key, value in overrides.items():
                os.environ[key] = value
            return self

        def __exit__(self, exc_type, exc, tb):
            for key, old_value in previous.items():
                if old_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_value
            return False

    return _EnvOverrideContext()


def _build_child_env(*, proxy_url: str, mysql_env: dict[str, str], model_defaults: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(_proxy_env_overrides(proxy_url))
    env.update(mysql_env)
    env.update(_backend_model_env_overrides(model_defaults))
    env.update(_user_stability_env_overrides())
    return env


def _backend_model_env_overrides(model_defaults: dict[str, str]) -> dict[str, str]:
    return {
        "CRS_AGENT_MODEL": model_defaults["agent_model"],
        "CRS_OPENROUTER_CLARIFY_MODEL": model_defaults["openrouter_clarify_model"],
        "CRS_INTENT_ROUTER_MODEL": model_defaults["intent_router_model"],
        "CRS_CODING_ENGINE_MODEL": model_defaults["coding_engine_model"],
    }


def _sync_backend_model_configs(*, backend_dir: Path, model_defaults: dict[str, str]) -> dict[str, str]:
    backend_dir = backend_dir.resolve()
    inserted_backend_dir = False
    backend_dir_str = str(backend_dir)
    if backend_dir_str not in sys.path:
        sys.path.insert(0, backend_dir_str)
        inserted_backend_dir = True

    try:
        from app.agent.model_ids import normalize_configured_model
        from app.legacy.models.admin_models import SystemConfig
        from app.legacy.models.database import get_session_local
        from app.legacy.services.config_initializer import ACTIVE_SYSTEM_CONFIGS, reconcile_system_configs

        metadata_by_key = {item["key"]: item for item in ACTIVE_SYSTEM_CONFIGS}
        desired_values = {
            "agent_model": normalize_configured_model(model_defaults["agent_model"]),
            "openrouter_clarify_model": normalize_configured_model(model_defaults["openrouter_clarify_model"]),
            "intent_router_model": normalize_configured_model(model_defaults["intent_router_model"]),
        }

        SessionLocal = get_session_local()
        db = SessionLocal()
        changed: dict[str, str] = {}
        try:
            reconcile_system_configs(db)
            existing = {
                row.config_key: row
                for row in db.query(SystemConfig).filter(
                    SystemConfig.config_key.in_(list(desired_values))
                ).all()
            }

            for key, value in desired_values.items():
                config = existing.get(key)
                if config is None:
                    item = metadata_by_key.get(key)
                    if item is None:
                        continue
                    config = SystemConfig(
                        config_key=key,
                        config_value=value,
                        config_type=str(item["type"]),
                        category=str(item["category"]),
                        description=str(item["description"]),
                        is_sensitive=bool(item.get("is_sensitive", False)),
                        updated_by="benchmark",
                    )
                    db.add(config)
                    changed[key] = value
                    continue

                current_value = str(config.config_value or "").strip()
                if current_value != value:
                    config.config_value = value
                    config.updated_by = "benchmark"
                    changed[key] = value

            if changed:
                db.commit()
            return changed
        finally:
            db.close()
    finally:
        if inserted_backend_dir:
            try:
                sys.path.remove(backend_dir_str)
            except ValueError:
                pass


def _forge_admin_refresh_token(*, backend_dir: Path) -> str:
    backend_dir = backend_dir.resolve()
    inserted_backend_dir = False
    backend_dir_str = str(backend_dir)
    if backend_dir_str not in sys.path:
        sys.path.insert(0, backend_dir_str)
        inserted_backend_dir = True

    try:
        from app.legacy.models.admin_models import AdminUser
        from app.legacy.models.database import get_session_local
        from app.legacy.utils.auth import create_access_token

        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            user = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
            if user is None:
                raise RuntimeError("no admin user found in backend database")
            return create_access_token(
                data={
                    "sub": user.username,
                    "user_id": user.id,
                    "role": user.role,
                }
            )
        finally:
            db.close()
    finally:
        if inserted_backend_dir:
            try:
                sys.path.remove(backend_dir_str)
            except ValueError:
                pass


def _refresh_backend_config_cache(*, base_url: str, app_token: str) -> None:
    endpoint = base_url.rstrip("/") + "/admin/config/refresh"
    req = urllib_request.Request(
        endpoint,
        data=b"",
        headers={
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=15.0) as resp:
        status = int(getattr(resp, "status", 200))
        if status != 200:
            raise RuntimeError(f"config refresh failed with HTTP {status}")


def _wait_health(base_url: str, timeout_seconds: float = 60.0) -> bool:
    endpoint = base_url.rstrip("/") + "/chat/health"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            req = urllib_request.Request(endpoint, method="GET")
            with urllib_request.urlopen(req, timeout=2.0) as resp:
                if int(getattr(resp, "status", 200)) == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _can_bind_local_port(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", int(port)))
            return True
    except Exception:
        return False


def _pick_managed_backend_port(preferred_port: int, max_offset: int = 20) -> int | None:
    preferred = int(preferred_port)
    if _can_bind_local_port(preferred):
        return preferred
    for offset in range(1, max(1, int(max_offset)) + 1):
        candidate = preferred + offset
        if _can_bind_local_port(candidate):
            return candidate
    return None


def _local_base_url_for_port(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def _looks_like_openrouter_model(raw_value: str | None) -> bool:
    value = str(raw_value or "").strip().lower()
    return value.startswith("openrouter:") or value.startswith("openrouter/")


def _requires_openrouter_transport(*, model_defaults: dict[str, str], args: argparse.Namespace) -> bool:
    configured_models = [
        model_defaults.get("agent_model"),
        model_defaults.get("openrouter_clarify_model"),
        model_defaults.get("intent_router_model"),
        model_defaults.get("coding_engine_model"),
        str(getattr(args, "user_model", "") or ""),
    ]
    return any(_looks_like_openrouter_model(item) for item in configured_models)


def _probe_proxy_listener(proxy_url: str) -> dict[str, Any]:
    normalized_proxy_url = str(proxy_url or "").strip()
    if not normalized_proxy_url:
        return {
            "ok": True,
            "skipped": True,
            "detail": "未显式配置代理地址，跳过本地代理监听检查。",
        }
    parsed = urllib_parse.urlparse(normalized_proxy_url)
    host = str(parsed.hostname or "").strip()
    port = parsed.port
    if not host or port is None:
        return {
            "ok": False,
            "proxy_url": normalized_proxy_url,
            "detail": "代理地址格式无效，必须包含 host 与 port。",
        }
    try:
        with socket.create_connection((host, int(port)), timeout=3.0):
            return {
                "ok": True,
                "proxy_url": normalized_proxy_url,
                "host": host,
                "port": int(port),
            }
    except Exception as exc:
        return {
            "ok": False,
            "proxy_url": normalized_proxy_url,
            "host": host,
            "port": int(port),
            "detail": str(exc),
        }


def _probe_openrouter_transport(*, env: dict[str, str]) -> dict[str, Any]:
    endpoint = str(env.get("BENCHMARK_OPENROUTER_PREFLIGHT_URL") or DEFAULT_OPENROUTER_PREFLIGHT_URL).strip()
    headers = {
        "User-Agent": "CRS-benchmark-one-click/1.0",
    }
    api_key = str(env.get("OPENROUTER_API_KEY") or env.get("CRS_OPENROUTER_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    proxies: dict[str, str] = {}
    https_proxy = str(env.get("HTTPS_PROXY") or env.get("https_proxy") or "").strip()
    http_proxy = str(env.get("HTTP_PROXY") or env.get("http_proxy") or "").strip()
    all_proxy = str(env.get("ALL_PROXY") or env.get("all_proxy") or "").strip()
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    elif all_proxy:
        proxies["https"] = all_proxy

    req = urllib_request.Request(endpoint, headers=headers, method="GET")
    opener = urllib_request.build_opener(urllib_request.ProxyHandler(proxies))
    timeout_seconds = 15.0
    retry_attempts = max(
        1,
        int(env.get("BENCHMARK_OPENROUTER_PREFLIGHT_RETRIES") or DEFAULT_OPENROUTER_PREFLIGHT_RETRIES),
    )
    retry_backoff_seconds = max(
        0.0,
        float(
            env.get("BENCHMARK_OPENROUTER_PREFLIGHT_BACKOFF_SECONDS")
            or DEFAULT_OPENROUTER_PREFLIGHT_BACKOFF_SECONDS
        ),
    )
    attempt_logs: list[dict[str, Any]] = []

    def _retryable_transport_error(detail: str) -> bool:
        lowered = str(detail or "").strip().lower()
        return any(marker in lowered for marker in _RETRYABLE_TRANSPORT_ERROR_MARKERS)

    for attempt in range(1, retry_attempts + 1):
        try:
            with opener.open(req, timeout=timeout_seconds) as resp:
                status = int(getattr(resp, "status", 200))
                attempt_logs.append(
                    {
                        "attempt": attempt,
                        "ok": True,
                        "http_status": status,
                    }
                )
                return {
                    "ok": True,
                    "url": endpoint,
                    "http_status": status,
                    "proxies": proxies,
                    "attempts": attempt_logs,
                }
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            ok = 400 <= status < 500
            attempt_logs.append(
                {
                    "attempt": attempt,
                    "ok": ok,
                    "http_status": status,
                    "detail": str(exc),
                }
            )
            return {
                "ok": ok,
                "url": endpoint,
                "http_status": status,
                "detail": str(exc),
                "proxies": proxies,
                "attempts": attempt_logs,
            }
        except Exception as exc:
            detail = str(exc)
            attempt_logs.append(
                {
                    "attempt": attempt,
                    "ok": False,
                    "detail": detail,
                }
            )
            if attempt >= retry_attempts or not _retryable_transport_error(detail):
                return {
                    "ok": False,
                    "url": endpoint,
                    "detail": detail,
                    "proxies": proxies,
                    "attempts": attempt_logs,
                }
            time.sleep(retry_backoff_seconds * attempt)

    return {
        "ok": False,
        "url": endpoint,
        "detail": "openrouter preflight exhausted retries",
        "proxies": proxies,
        "attempts": attempt_logs,
    }


def _encode_multipart_formdata(
    *,
    payload: dict[str, Any],
    files: list[tuple[str, str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = f"----CRSBenchmarkBoundary{int(time.time() * 1000)}"
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


def _build_probe_files(image_paths: list[Path]) -> list[tuple[str, str, bytes, str]]:
    files: list[tuple[str, str, bytes, str]] = []
    for path in image_paths:
        content_type, _ = mimetypes.guess_type(str(path))
        files.append(
            (
                "images",
                path.name,
                path.read_bytes(),
                content_type or "image/jpeg",
            )
        )
    return files


def _build_image_probe_question(case: Any) -> str:
    initial_user_message = getattr(case, "initial_user_message", None)
    if isinstance(initial_user_message, str):
        normalized = initial_user_message.strip()
        if normalized:
            return (
                "请先根据当前图片做一次简短识别，并结合这条用户原始问题判断信息是否足够："
                f"{normalized}"
                "；如果信息不足，请直接发起一个澄清问题。"
            )
    question_text = getattr(case, "question_text", None)
    if isinstance(question_text, str):
        normalized = question_text.strip()
        if normalized:
            return (
                "请先根据当前图片做一次简短识别，并结合这条用户问题判断信息是否足够："
                f"{normalized}"
                "；如果信息不足，请直接发起一个澄清问题。"
            )
    return DEFAULT_IMAGE_PROBE_QUESTION


def _probe_image_chat(
    *,
    base_url: str,
    app_token: str,
    image_paths: list[Path],
    question_text: str,
    timeout_ms: int,
) -> tuple[bool, int | None, str]:
    endpoint = base_url.rstrip("/") + "/chat/completions-with-images"
    payload = {
        "message": question_text,
        "context": {},
        "mode": "doc_search",
        "client_type": "benchmark",
    }
    files = _build_probe_files(image_paths)
    body, boundary = _encode_multipart_formdata(payload=payload, files=files)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "x-app-token": app_token,
    }
    req = urllib_request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=timeout_ms / 1000.0) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read().decode("utf-8", errors="replace")
            ok = status == 200
            return ok, status, raw[:600]
    except urllib_error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return False, int(exc.code), raw[:600]
    except Exception as exc:  # pragma: no cover
        return False, None, str(exc)


def _run_image_probe(
    *,
    base_url: str,
    app_token: str,
    image_paths: list[Path],
    question_text: str,
    timeout_ms: int,
    retries: int,
    min_successes: int,
    retry_sleep_seconds: float = 1.5,
) -> tuple[bool, list[dict[str, Any]]]:
    attempt_logs: list[dict[str, Any]] = []
    success_count = 0
    attempt_limit = max(1, int(retries))
    success_target = max(1, int(min_successes))

    for attempt in range(1, attempt_limit + 1):
        ok, status, detail = _probe_image_chat(
            base_url=base_url,
            app_token=app_token,
            image_paths=image_paths,
            question_text=question_text,
            timeout_ms=timeout_ms,
        )
        if ok:
            success_count += 1
        attempt_logs.append(
            {
                "attempt": attempt,
                "ok": ok,
                "status": status,
                "detail": detail,
                "success_count": success_count,
                "required_successes": success_target,
                "timeout_ms": int(timeout_ms),
            }
        )
        if success_count >= success_target:
            return True, attempt_logs
        if attempt < attempt_limit:
            time.sleep(max(0.0, float(retry_sleep_seconds)))

    return False, attempt_logs


def _start_backend(
    *,
    backend_dir: Path,
    backend_port: int,
    image_model: str,
    image_max_images: int,
    proxy_url: str,
    mysql_env: dict[str, str],
    model_defaults: dict[str, str],
    stdout_log_path: Path,
    stderr_log_path: Path,
) -> subprocess.Popen:
    env = _build_child_env(proxy_url=proxy_url, mysql_env=mysql_env, model_defaults=model_defaults)
    env["CRS_IMAGE_EVIDENCE_MODEL"] = image_model
    env["CRS_IMAGE_EVIDENCE_MAX_IMAGES"] = str(image_max_images)
    stdout_log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_log = open(stdout_log_path, "w", encoding="utf-8")
    stderr_log = open(stderr_log_path, "w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(backend_port),
            ],
            cwd=str(backend_dir),
            env=env,
            stdout=stdout_log,
            stderr=stderr_log,
        )
    finally:
        stdout_log.close()
        stderr_log.close()
    return proc


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def _extract_http_runtime_error_count(score_report_path: Path | None) -> int | None:
    if score_report_path is None or not score_report_path.exists():
        return None
    report = _read_json_file(score_report_path)
    if not isinstance(report, dict):
        return None
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    attempt_level = summary.get("attempt_level")
    if not isinstance(attempt_level, dict):
        return None
    failures = attempt_level.get("failures")
    if not isinstance(failures, dict):
        return None
    blocking = failures.get("blocking_failure_counts")
    if not isinstance(blocking, dict):
        return None
    value = blocking.get("HTTP_OR_RUNTIME_ERROR")
    if value is None:
        return 0
    try:
        return int(value)
    except Exception:
        return None


def _extract_incomplete_execution_count(score_report_path: Path | None) -> int | None:
    if score_report_path is None or not score_report_path.exists():
        return None
    report = _read_json_file(score_report_path)
    if not isinstance(report, dict):
        return None
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    attempt_level = summary.get("attempt_level")
    if not isinstance(attempt_level, dict):
        return None
    failures = attempt_level.get("failures")
    if not isinstance(failures, dict):
        return None
    final_status_counts = failures.get("final_status_counts")
    if not isinstance(final_status_counts, dict):
        return 0
    total = 0
    for status, raw_value in final_status_counts.items():
        if str(status) not in _INCOMPLETE_FINAL_STATUSES:
            continue
        try:
            total += int(raw_value)
        except Exception:
            return None
    return total


def _parse_one_click_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    resolved_user_defaults = resolve_user_model_defaults()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--one-click", dest="one_click", action="store_true")
    parser.add_argument("--one-click-train", dest="one_click", action="store_true")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--split", default="train")
    parser.add_argument("--suite", action="append", default=[])
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--base-url", default="http://127.0.0.1:8006")
    parser.add_argument("--backend-port", type=int, default=8006)
    parser.add_argument(
        "--backend-dir",
        default=str(_repo_root() / "modules" / "crs-agent-upstream" / "backend"),
    )
    parser.add_argument(
        "--token-file",
        default=str(_repo_root() / ".local" / "benchmark_app_token.txt"),
    )
    parser.add_argument("--vpn-exe", default=DEFAULT_SAKURACAT_EXE)
    parser.add_argument("--timeout-ms", type=int, default=240000)
    parser.add_argument("--max-attempts-per-case", type=int, default=1)
    parser.add_argument("--user-model", default=resolved_user_defaults.model)
    parser.add_argument("--user-provider", default=resolved_user_defaults.provider)
    parser.add_argument("--proxy-url", default=DEFAULT_PROXY_URL)
    parser.add_argument("--image-max-images", type=int, default=8)
    parser.add_argument(
        "--image-model-candidates",
        default=(
            "qwen/qwen3-vl-32b-instruct,"
            "qwen/qwen3-vl-30b-a3b-instruct,"
            "qwen/qwen3-vl-8b-instruct"
        ),
    )
    parser.add_argument("--probe-timeout-ms", type=int, default=180000)
    parser.add_argument("--probe-retries", type=int, default=3)
    parser.add_argument("--probe-min-successes", type=int, default=1)
    parser.add_argument("--round-retries", type=int, default=2)
    args, remaining = parser.parse_known_args(argv)
    return args, remaining


def _resolve_probe_target(
    *,
    repo_root: Path,
    split: str,
    suite_filters: list[str],
    case_filters: list[str],
) -> tuple[Path, str, str] | None:
    suites = filter_suites(TASK_SUITES_BY_SPLIT.get(split, []), suite_filters, case_filters)
    for suite in suites:
        for case in suite.cases:
            question_images = list(getattr(case, "question_images", []) or [])
            if not question_images:
                continue
            for raw_path in question_images:
                image_path = Path(raw_path)
                if not image_path.is_absolute():
                    image_path = (repo_root / image_path).resolve()
                if image_path.exists():
                    probe_question = _build_image_probe_question(case)
                    return image_path, probe_question, str(case.case_id)
    return None


def _build_benchmark_command(
    *,
    base_url: str,
    token: str,
    args: argparse.Namespace,
) -> list[str]:
    resolved_user_model = args.user_model
    resolved_user_provider = args.user_provider
    if args.user_model:
        resolved_user_model, resolved_user_provider = _resolve_completion_target(
            model=str(args.user_model),
            provider=str(args.user_provider) if args.user_provider else None,
        )

    cmd = [
        sys.executable,
        str(CURRENT_DIR / "run.py"),
        "--split",
        args.split,
        "--base-url",
        base_url,
        "--app-token",
        token,
        "--timeout-ms",
        str(args.timeout_ms),
        "--request-mode",
        "doc_search",
        "--max-attempts-per-case",
        str(args.max_attempts_per_case),
    ]
    if resolved_user_model:
        cmd.extend(["--user-model", str(resolved_user_model)])
    if resolved_user_provider:
        cmd.extend(["--user-provider", str(resolved_user_provider)])
    for suite_id in args.suite:
        if suite_id:
            cmd.extend(["--suite", str(suite_id)])
    for case_id in args.case_id:
        if case_id:
            cmd.extend(["--case-id", str(case_id)])
    return cmd


def _export_round_review_html(
    *,
    report_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    script_path = CURRENT_DIR / "doc_search_bench" / "chat_export" / "render_round_case_review_html.py"
    if not script_path.exists():
        return {
            "ok": False,
            "reason": "review_script_missing",
            "script_path": str(script_path),
            "report_path": str(report_path),
            "output_path": str(output_path),
        }
    if not report_path.exists():
        return {
            "ok": False,
            "reason": "report_missing",
            "report_path": str(report_path),
            "output_path": str(output_path),
        }

    cmd = [
        sys.executable,
        str(script_path),
        "--report",
        str(report_path),
        "--output",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        timeout=600,
    )
    export_result: dict[str, Any] = {
        "script_path": str(script_path),
        "report_path": str(report_path),
        "output_path": str(output_path),
        "returncode": result.returncode,
        "stdout": result.stdout[:1200],
        "stderr": result.stderr[:1200],
        "ok": result.returncode == 0 and output_path.exists(),
    }
    if not export_result["ok"]:
        export_result["reason"] = "review_export_failed"
    return export_result


def _run_one_click(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    backend_dir = Path(args.backend_dir).resolve()
    token_file = Path(args.token_file).resolve()
    base_url = args.base_url.rstrip("/")
    active_base_url = base_url
    model_defaults = _resolve_backend_model_defaults()
    one_click_run_id = _make_one_click_run_id()
    vpn_exe_path = Path(str(args.vpn_exe)).resolve()

    proxy_bootstrap = _ensure_proxy_ready(proxy_url=str(args.proxy_url), vpn_exe_path=vpn_exe_path)
    if not bool(proxy_bootstrap.get("ok")):
        print(
            json.dumps(
                {
                    "one_click": False,
                    "reason": "proxy_bootstrap_failed",
                    "proxy_bootstrap": proxy_bootstrap,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    token_status = _token_file_status(token_file)
    if not bool(token_status.get("ok")):
        print(
            json.dumps(
                {
                    "one_click": False,
                    "reason": str(token_status.get("reason") or "token_invalid"),
                    "token_status": token_status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    token = _read_token(token_file)

    backend_openrouter_status = _backend_openrouter_env_status()
    if _requires_openrouter_transport(model_defaults=model_defaults, args=args) and not bool(
        backend_openrouter_status.get("ok")
    ):
        print(
            json.dumps(
                {
                    "one_click": False,
                    "reason": str(backend_openrouter_status.get("reason") or "openrouter_env_invalid"),
                    "backend_openrouter_status": backend_openrouter_status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    redis_prepare = ensure_local_redis_running()
    if not bool(redis_prepare.get("ready")):
        print(
            json.dumps(
                {
                    "one_click": False,
                    "reason": "redis_not_ready",
                    "redis_prepare": redis_prepare,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    mysql_prepare = _ensure_local_mysql_running(repo_root=repo_root)
    if not bool(mysql_prepare.get("ready")):
        print(
            json.dumps(
                {
                    "one_click": False,
                    "reason": "mysql_not_ready",
                    "mysql_prepare": mysql_prepare,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    mysql_config = _read_repo_mysql_config(repo_root=repo_root)
    mysql_env = _repo_mysql_env_overrides(mysql_config, repo_root=repo_root)
    child_env = _build_child_env(proxy_url=args.proxy_url, mysql_env=mysql_env, model_defaults=model_defaults)

    probe_target = _resolve_probe_target(
        repo_root=repo_root,
        split=str(args.split),
        suite_filters=[str(item) for item in getattr(args, "suite", []) if str(item).strip()],
        case_filters=[str(item) for item in getattr(args, "case_id", []) if str(item).strip()],
    )
    if probe_target is None:
        probe_image = None
        probe_question = None
        probe_case_id = None
    else:
        probe_image, probe_question, probe_case_id = probe_target
    if probe_image is not None and not probe_image.exists():
        print(
            json.dumps(
                {
                    "one_click": False,
                    "reason": "probe_image_missing",
                    "probe_image": str(probe_image),
                    "probe_case_id": probe_case_id,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    one_click_log_dir = repo_root / "benchmark" / "reports" / "one_click_logs" / one_click_run_id
    one_click_log_dir.mkdir(parents=True, exist_ok=True)
    candidates = [item.strip() for item in str(args.image_model_candidates).split(",") if item.strip()]
    backend_proc: subprocess.Popen | None = None
    selected_model: str | None = None
    probe_logs: list[dict[str, Any]] = []
    reused_existing_backend = False
    existing_backend_fallback = False
    managed_backend_port: int | None = None
    with _apply_env_overrides_temporarily(mysql_env):
        synced_backend_models = _sync_backend_model_configs(
            backend_dir=backend_dir,
            model_defaults=model_defaults,
        )
    proxy_probe = _probe_proxy_listener(str(args.proxy_url))
    openrouter_preflight: dict[str, Any] | None = None
    if _requires_openrouter_transport(model_defaults=model_defaults, args=args):
        if not proxy_probe.get("ok"):
            print(
                json.dumps(
                    {
                        "one_click": False,
                        "reason": "proxy_not_ready",
                        "proxy_probe": proxy_probe,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        openrouter_preflight = _probe_openrouter_transport(env=child_env)
        if not bool(openrouter_preflight.get("ok")):
            print(
                json.dumps(
                    {
                        "one_click": False,
                        "reason": "openrouter_transport_not_ready",
                        "proxy_probe": proxy_probe,
                        "openrouter_preflight": openrouter_preflight,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2

    try:
        existing_backend_ready = _wait_health(base_url=base_url, timeout_seconds=2.0)
        if existing_backend_ready:
            reused_existing_backend = True
            with _apply_env_overrides_temporarily(mysql_env):
                refresh_token = _forge_admin_refresh_token(backend_dir=backend_dir)
            _refresh_backend_config_cache(base_url=base_url, app_token=refresh_token)
            selected_model = "existing_backend"
            existing_probe = {
                "model": selected_model,
                "health_ok": True,
                "attempts": [],
                "case_id": probe_case_id,
                "required_successes": 1,
            }
            probe_ok = True
            if probe_image is not None and probe_question is not None:
                probe_ok, attempt_logs = _run_image_probe(
                    base_url=base_url,
                    app_token=token,
                    image_paths=[probe_image],
                    question_text=probe_question,
                    timeout_ms=int(args.probe_timeout_ms),
                    retries=int(args.probe_retries),
                    min_successes=1,
                )
                existing_probe["attempts"].extend(attempt_logs)
            probe_logs.append(existing_probe)
            if not probe_ok:
                reused_existing_backend = False
                existing_backend_fallback = True
                selected_model = None
        if not reused_existing_backend:
            launch_port = _pick_managed_backend_port(int(args.backend_port))
            if launch_port is None:
                print(
                    json.dumps(
                        {
                            "one_click": False,
                            "reason": "no_available_backend_port",
                            "preferred_backend_port": int(args.backend_port),
                            "existing_backend_fallback": existing_backend_fallback,
                            "probe_logs": probe_logs,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2
            managed_backend_port = launch_port
            active_base_url = _local_base_url_for_port(managed_backend_port)
            if not candidates:
                print(
                    json.dumps(
                        {
                            "one_click": False,
                            "reason": "no_image_model_candidates",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2

            for model_name in candidates:
                model_probe = {
                    "model": model_name,
                    "health_ok": False,
                    "attempts": [],
                    "case_id": probe_case_id,
                    "required_successes": max(1, int(args.probe_min_successes)),
                    "managed_backend_port": managed_backend_port,
                }
                backend_proc = _start_backend(
                    backend_dir=backend_dir,
                    backend_port=int(managed_backend_port),
                    image_model=model_name,
                    image_max_images=args.image_max_images,
                    proxy_url=args.proxy_url,
                    mysql_env=mysql_env,
                    model_defaults=model_defaults,
                    stdout_log_path=one_click_log_dir / f"backend-probe-{model_name.replace('/', '_')}.stdout.log",
                    stderr_log_path=one_click_log_dir / f"backend-probe-{model_name.replace('/', '_')}.stderr.log",
                )
                health_ok = _wait_health(base_url=active_base_url, timeout_seconds=60.0)
                model_probe["health_ok"] = health_ok
                if not health_ok:
                    probe_logs.append(model_probe)
                    _terminate_process(backend_proc)
                    backend_proc = None
                    continue

                probe_ok = True
                if probe_image is not None and probe_question is not None:
                    probe_ok, attempt_logs = _run_image_probe(
                        base_url=active_base_url,
                        app_token=token,
                        image_paths=[probe_image],
                        question_text=probe_question,
                        timeout_ms=int(args.probe_timeout_ms),
                        retries=int(args.probe_retries),
                        min_successes=int(args.probe_min_successes),
                    )
                    model_probe["attempts"].extend(attempt_logs)

                probe_logs.append(model_probe)
                if probe_ok:
                    selected_model = model_name
                    break

                _terminate_process(backend_proc)
                backend_proc = None

        if not selected_model or (backend_proc is None and not reused_existing_backend):
            print(
                json.dumps(
                    {
                        "one_click": False,
                        "reason": "no_working_image_model",
                        "probe_logs": probe_logs,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2

        run_summaries: list[dict[str, Any]] = []
        for round_index in range(1, max(1, int(args.rounds)) + 1):
            round_attempts: list[dict[str, Any]] = []
            chosen_attempt: dict[str, Any] | None = None
            max_round_attempts = max(1, int(args.round_retries) + 1)
            for round_attempt in range(1, max_round_attempts + 1):
                cmd = _build_benchmark_command(
                    base_url=active_base_url,
                    token=token,
                    args=args,
                )
                result = subprocess.run(
                    cmd,
                    cwd=str(repo_root),
                    env=child_env,
                    capture_output=True,
                    text=True,
                    timeout=7200,
                )
                parsed = _extract_json_object(result.stdout) or {"raw_stdout": result.stdout[:1200]}

                score_report_path: Path | None = None
                score_report_raw = parsed.get("score_report")
                if isinstance(score_report_raw, str) and score_report_raw.strip():
                    score_report_path = Path(score_report_raw.strip())
                runtime_error_count = _extract_http_runtime_error_count(score_report_path)
                incomplete_execution_count = _extract_incomplete_execution_count(score_report_path)
                execution_complete = bool(
                    runtime_error_count == 0
                    and incomplete_execution_count == 0
                    and score_report_path is not None
                    and score_report_path.exists()
                    and result.returncode in (0, 1)
                )
                attempt_summary = {
                    "attempt": round_attempt,
                    "returncode": result.returncode,
                    "parsed": parsed,
                    "stderr": result.stderr[:1200],
                    "runtime_error_count": runtime_error_count,
                    "incomplete_execution_count": incomplete_execution_count,
                    "execution_complete": execution_complete,
                }
                round_attempts.append(attempt_summary)
                if execution_complete:
                    chosen_attempt = attempt_summary
                    break
                time.sleep(2.0)

            if chosen_attempt is None and round_attempts:
                chosen_attempt = round_attempts[-1]
            review_html_export: dict[str, Any] | None = None
            if chosen_attempt is not None:
                parsed = chosen_attempt.get("parsed")
                if isinstance(parsed, dict):
                    actual_report_raw = parsed.get("actual_report")
                    if isinstance(actual_report_raw, str) and actual_report_raw.strip():
                        report_path = Path(actual_report_raw.strip()).resolve()
                        review_html_export = _export_round_review_html(
                            report_path=report_path,
                            output_path=report_path.parent / ROUND_REVIEW_HTML_FILENAME,
                        )
            run_summaries.append(
                {
                    "round": round_index,
                    "selected_attempt": chosen_attempt,
                    "attempts": round_attempts,
                    "review_html_export": review_html_export,
                }
            )

        print(
            json.dumps(
                {
                    "one_click": True,
                    "selected_image_model": selected_model,
                    "reused_existing_backend": reused_existing_backend,
                    "existing_backend_fallback": existing_backend_fallback,
                    "managed_backend_port": managed_backend_port,
                    "active_base_url": active_base_url,
                    "one_click_run_id": one_click_run_id,
                    "backend_model_defaults": model_defaults,
                    "synced_backend_models": synced_backend_models,
                    "user_runtime_env": _user_stability_env_overrides(),
                    "proxy_bootstrap": proxy_bootstrap,
                    "proxy_probe": proxy_probe,
                    "mysql_prepare": mysql_prepare,
                    "token_status": token_status,
                    "backend_openrouter_status": backend_openrouter_status,
                    "openrouter_preflight": openrouter_preflight,
                    "user_model_input": args.user_model,
                    "user_provider_input": args.user_provider,
                    "user_model_resolved": (
                        _resolve_completion_target(
                            model=str(args.user_model),
                            provider=str(args.user_provider) if args.user_provider else None,
                        )[0]
                        if args.user_model
                        else None
                    ),
                    "user_provider_resolved": (
                        _resolve_completion_target(
                            model=str(args.user_model),
                            provider=str(args.user_provider) if args.user_provider else None,
                        )[1]
                        if args.user_model
                        else None
                    ),
                    "user_openai_compat_fresh_client": True,
                    "suite_filters": list(args.suite),
                    "case_filters": list(args.case_id),
                    "probe_logs": probe_logs,
                    "redis_prepare": redis_prepare,
                    "run_summaries": run_summaries,
                    "logs_dir": str(one_click_log_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if not reused_existing_backend:
            _terminate_process(backend_proc)


if __name__ == "__main__":
    parsed_args, _remaining_args = _parse_one_click_args(sys.argv[1:])
    if parsed_args.one_click:
        raise SystemExit(_run_one_click(parsed_args))
    raise SystemExit(benchmark_main())
