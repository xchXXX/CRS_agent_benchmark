"""Request-scoped runtime dependency helpers."""

import logging

from fastapi import HTTPException, Request

from app.agent.runtime.deps import AgentRuntimeDeps
from app.core.config import settings
from app.legacy.services.token_identity_service import (
    TokenIdentityRequestError,
    TokenIdentityResponseError,
    TokenIdentityTimeoutError,
    TokenValidationResult,
)


logger = logging.getLogger(__name__)


def extract_app_token(headers, query_params=None) -> str | None:
    for name in ("x-app-token", "app-token", "appToken", "token"):
        value = headers.get(name)
        if value:
            return value
    if query_params is not None:
        for name in ("x-app-token", "app-token", "appToken", "token"):
            value = query_params.get(name)
            if value:
                return value
    return None


async def _validate_token_with_service(service, token: str) -> TokenValidationResult:
    if hasattr(service, "validate_token"):
        return await service.validate_token(token)

    user_id = await service.resolve_user_id(token)
    if user_id is None:
        return TokenValidationResult(valid=False, message="登录已失效，请重新登录")
    return TokenValidationResult(valid=True, user_id=user_id, message="ok")


async def _resolve_user_id_with_service(service, token: str) -> int | None:
    if hasattr(service, "resolve_user_id"):
        return await service.resolve_user_id(token)

    validation = await _validate_token_with_service(service, token)
    if not validation.valid:
        return None
    return validation.user_id


async def build_request_runtime_deps(request: Request) -> AgentRuntimeDeps:
    base_deps = getattr(request.app.state, "runtime_deps", None)
    if base_deps is None:
        base_deps = AgentRuntimeDeps.build_default()
        request.app.state.runtime_deps = base_deps

    app_token = extract_app_token(request.headers, request.query_params)
    user_id = getattr(base_deps, "user_id", None)
    token_identity_service = getattr(base_deps, "token_identity_service", None)

    if app_token and token_identity_service is not None:
        try:
            user_id = await _resolve_user_id_with_service(token_identity_service, app_token)
        except TokenIdentityTimeoutError as exc:
            logger.warning("request identity resolve timeout, fallback to raw token only: %s", exc)
        except (TokenIdentityRequestError, TokenIdentityResponseError) as exc:
            logger.warning("request identity resolve failed, fallback to raw token only: %s", exc)
        except Exception as exc:
            logger.warning("request identity resolve unexpected failure, fallback to raw token only: %s", exc)

    if settings.user_auth_enabled and not app_token:
        logger.info("request arrived without app token while user auth is enabled")

    return base_deps.clone_for_request(
        app_token=app_token,
        user_id=user_id,
        enforce_external_doc_search=True,
        tracer=base_deps.tracer.fork(),
    )
