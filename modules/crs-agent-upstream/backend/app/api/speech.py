"""Aliyun speech compatibility endpoints."""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request

from app.api.request_context import build_request_runtime_deps
from app.core.config import settings

router = APIRouter(tags=["speech"])


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _percent_encode(value: str) -> str:
    return quote(str(value), safe="~")


def _build_signature(params: dict[str, str], access_key_secret: str) -> str:
    canonicalized = "&".join(
        f"{_percent_encode(key)}={_percent_encode(value)}" for key, value in sorted(params.items(), key=lambda item: item[0])
    )
    string_to_sign = f"GET&%2F&{_percent_encode(canonicalized)}"
    digest = hmac.new(
        f"{access_key_secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


async def _fetch_aliyun_speech_token() -> dict:
    params = {
        "AccessKeyId": settings.aliyun_speech_access_key_id,
        "Action": "CreateToken",
        "Format": "JSON",
        "RegionId": settings.aliyun_speech_region_id,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": _utc_timestamp(),
        "Version": "2019-02-28",
    }
    params["Signature"] = _build_signature(params, settings.aliyun_speech_access_key_secret)

    timeout = max(3, int(settings.aliyun_speech_timeout_seconds))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(settings.aliyun_speech_token_url, params=params)
        response.raise_for_status()
        payload = response.json()

    token = ((payload.get("Token") or {}).get("Id") or "").strip()
    expire_time = int((payload.get("Token") or {}).get("ExpireTime") or 0)
    if not token or not expire_time:
        raise RuntimeError("阿里云语音 Token 响应缺少必要字段")

    return {
        "success": True,
        "token": token,
        "expire_time": expire_time,
        "app_key": settings.aliyun_speech_app_key,
        "ws_url": settings.aliyun_speech_ws_url,
    }


@router.get("/speech/aliyun/token")
@router.get("/chat/api/speech/aliyun/token")
async def get_aliyun_speech_token(raw_request: Request) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)

    enabled = settings.aliyun_speech_enabled
    if runtime_deps.config_service is not None:
        enabled = bool(runtime_deps.config_service.get("aliyun_speech_enabled", enabled))

    access_key_id = (
        runtime_deps.config_service.get("aliyun_speech_access_key_id", settings.aliyun_speech_access_key_id)
        if runtime_deps.config_service is not None
        else settings.aliyun_speech_access_key_id
    )
    access_key_secret = (
        runtime_deps.config_service.get("aliyun_speech_access_key_secret", settings.aliyun_speech_access_key_secret)
        if runtime_deps.config_service is not None
        else settings.aliyun_speech_access_key_secret
    )
    app_key = (
        runtime_deps.config_service.get("aliyun_speech_app_key", settings.aliyun_speech_app_key)
        if runtime_deps.config_service is not None
        else settings.aliyun_speech_app_key
    )

    if not enabled:
        return {
            "success": False,
            "error": "阿里云语音识别未启用",
        }

    if not str(access_key_id or "").strip() or not str(access_key_secret or "").strip() or not str(app_key or "").strip():
        return {
            "success": False,
            "error": "阿里云语音识别配置不完整",
        }

    original_access_key_id = settings.aliyun_speech_access_key_id
    original_access_key_secret = settings.aliyun_speech_access_key_secret
    original_app_key = settings.aliyun_speech_app_key
    try:
        settings.aliyun_speech_access_key_id = str(access_key_id or "").strip()
        settings.aliyun_speech_access_key_secret = str(access_key_secret or "").strip()
        settings.aliyun_speech_app_key = str(app_key or "").strip()
        return await _fetch_aliyun_speech_token()
    except Exception as exc:
        return {
            "success": False,
            "error": f"阿里云语音 Token 获取失败: {exc}",
        }
    finally:
        settings.aliyun_speech_access_key_id = original_access_key_id
        settings.aliyun_speech_access_key_secret = original_access_key_secret
        settings.aliyun_speech_app_key = original_app_key
