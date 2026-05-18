"""Legacy auth/token helper endpoints used by the user frontend."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.legacy.services.token_identity_service import (
    TokenIdentityRequestError,
    TokenIdentityResponseError,
    TokenIdentityTimeoutError,
    TokenValidationResult,
    token_identity_service,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["legacy-proxy"])


class ValidateTokenRequest(BaseModel):
    token: str


def _mask_token(token: str | None, head: int = 20) -> str | None:
    if not token:
        return None
    if len(token) <= head:
        return token
    return f"{token[:head]}..."


def _get_token_identity_service(request: Request):
    deps = getattr(request.app.state, "runtime_deps", None)
    service = getattr(deps, "token_identity_service", None) if deps is not None else None
    return service or token_identity_service


async def _validate_token_with_service(service, token: str) -> TokenValidationResult:
    if hasattr(service, "validate_token"):
        return await service.validate_token(token)

    user_id = await service.resolve_user_id(token)
    if user_id is None:
        return TokenValidationResult(valid=False, message="登录已失效")
    return TokenValidationResult(valid=True, user_id=user_id, message="ok")


@router.get("/legacy/auth-enabled")
@router.get("/chat/api/legacy/auth-enabled")
async def check_auth_enabled() -> dict[str, bool]:
    return {"enabled": True}


@router.post("/legacy/validate-token")
@router.post("/chat/api/legacy/validate-token")
async def validate_user_token(payload: ValidateTokenRequest, request: Request) -> dict[str, Any]:
    service = _get_token_identity_service(request)
    logger.info("[TokenValidateProxy] validating token=%s", _mask_token(payload.token, head=10))

    try:
        validation = await _validate_token_with_service(service, payload.token)
    except TokenIdentityTimeoutError as exc:
        logger.warning("[TokenValidateProxy] upstream timeout: %s", exc)
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (TokenIdentityRequestError, TokenIdentityResponseError) as exc:
        logger.warning("[TokenValidateProxy] upstream proxy failure: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[TokenValidateProxy] unexpected validation failure: %s", exc)
        raise HTTPException(status_code=502, detail=f"代理请求失败: {exc}") from exc

    if not validation.valid or validation.user_id is None:
        logger.warning("[TokenValidateProxy] token invalid token=%s", _mask_token(payload.token, head=10))
        return {"valid": False, "message": validation.message or "登录已失效"}

    logger.info("[TokenValidateProxy] token valid user_id=%s", validation.user_id)
    return {"valid": True, "userId": int(validation.user_id)}


@router.get("/legacy/extract-token")
@router.get("/chat/api/legacy/extract-token")
async def extract_token_from_header(request: Request) -> dict[str, str | None]:
    token = (
        request.headers.get("app-token")
        or request.headers.get("appToken")
        or request.headers.get("token")
        or request.headers.get("x-app-token")
    )
    if token:
        logger.info("[ExtractToken] extracted token=%s", _mask_token(token, head=10))
        return {"token": token}
    return {"token": None}


@router.get("/legacy/token-diagnose")
@router.get("/chat/api/legacy/token-diagnose")
async def diagnose_token_sources(request: Request) -> dict[str, Any]:
    headers_info: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in ("host", "connection", "accept-encoding", "accept-language"):
            continue
        headers_info[key] = value[:40] + "..." if len(value) > 40 else value

    token_headers: dict[str, str] = {}
    for name in ("app-token", "apptoken", "appToken", "token", "authorization", "x-app-token", "x-token"):
        val = request.headers.get(name)
        if val:
            token_headers[name] = _mask_token(val)

    cookies: dict[str, str] = {}
    for key, value in request.cookies.items():
        cookies[key] = _mask_token(value)

    return {
        "token_headers": token_headers,
        "all_headers": headers_info,
        "query_params": dict(request.query_params),
        "cookies": cookies,
        "client_ip": request.client.host if request.client else None,
        "url": str(request.url),
        "method": request.method,
    }
