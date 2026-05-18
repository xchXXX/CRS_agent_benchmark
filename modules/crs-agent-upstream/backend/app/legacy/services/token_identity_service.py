"""Resolve app-token identities through upstream member API."""

from dataclasses import dataclass
import hashlib
import logging
from typing import Optional

import httpx
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import Settings, settings as app_settings
from app.legacy.utils.token_utils import parse_jwt_source


logger = logging.getLogger(__name__)


class TokenIdentityUpstreamError(Exception):
    """Base error for upstream token validation failures."""


class TokenIdentityTimeoutError(TokenIdentityUpstreamError):
    """Raised when upstream member API times out."""


class TokenIdentityRequestError(TokenIdentityUpstreamError):
    """Raised when upstream member API cannot be reached."""


class TokenIdentityResponseError(TokenIdentityUpstreamError):
    """Raised when upstream member API returns an invalid response."""


@dataclass(frozen=True)
class TokenValidationResult:
    """Normalized token validation result for legacy-compatible auth flows."""

    valid: bool
    user_id: Optional[int] = None
    message: str = "登录已失效"


class TokenIdentityService:
    """Resolve app-token to user_id with Redis caching."""

    MEMBER_API_BASE = "https://wx.51gonggui.com/commonrail/api/member-api"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        redis_client: Redis | None = None,
        member_api_base: str | None = None,
        timeout: httpx.Timeout | None = None,
    ):
        self._settings = settings or app_settings
        self._redis_client = redis_client
        self._member_api_base = member_api_base or self.MEMBER_API_BASE
        self._timeout = timeout or httpx.Timeout(15.0, connect=5.0)

    def _cache_key(self, token: str) -> str:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{self._settings.redis_key_prefix}:token_identity:user_id:{token_hash}"

    def _get_redis_client(self) -> Redis | None:
        if self._redis_client is not None:
            return self._redis_client
        redis_url = self._settings.redis_url
        if not redis_url:
            return None
        self._redis_client = Redis.from_url(redis_url, decode_responses=True)
        return self._redis_client

    def cache_user_id(self, token: Optional[str], user_id: Optional[int]) -> None:
        if not token or user_id is None:
            return

        client = self._get_redis_client()
        if client is None:
            return

        try:
            client.set(
                self._cache_key(token),
                str(int(user_id)),
                ex=self._settings.token_user_cache_ttl,
            )
        except (RedisError, ValueError, TypeError) as exc:
            logger.warning("cache token user_id failed: %s", exc)

    def get_cached_user_id(self, token: Optional[str]) -> Optional[int]:
        if not token:
            return None

        client = self._get_redis_client()
        if client is None:
            return None

        try:
            cached = client.get(self._cache_key(token))
        except RedisError as exc:
            logger.warning("read token user_id cache failed: %s", exc)
            return None

        if cached is None:
            return None

        try:
            return int(cached)
        except (ValueError, TypeError):
            logger.warning("invalid token user_id cache value ignored")
            return None

    async def resolve_user_id(self, token: Optional[str]) -> Optional[int]:
        if not token:
            return None

        try:
            validation = await self.validate_token(token)
        except TokenIdentityUpstreamError as exc:
            logger.warning("resolve user_id request failed: %s", exc)
            return None

        if not validation.valid:
            return None
        return validation.user_id

    async def validate_token(self, token: Optional[str]) -> TokenValidationResult:
        if not token:
            return TokenValidationResult(valid=False, message="未登录，请重新进入")

        cached_user_id = self.get_cached_user_id(token)
        if cached_user_id is not None:
            return TokenValidationResult(valid=True, user_id=cached_user_id, message="ok")

        data = await self._fetch_user_login_info_from_upstream(token)
        if not (
            isinstance(data, dict)
            and data.get("status") == 200
            and isinstance(data.get("data"), dict)
            and data["data"].get("userId") is not None
        ):
            return TokenValidationResult(
                valid=False,
                message=str(data.get("msg") or "登录已失效") if isinstance(data, dict) else "登录已失效",
            )

        try:
            user_id = int(data["data"]["userId"])
        except (ValueError, TypeError) as exc:
            logger.warning("validate token failed: invalid userId")
            raise TokenIdentityResponseError("上游服务响应格式异常") from exc

        self.cache_user_id(token, user_id)
        return TokenValidationResult(valid=True, user_id=user_id, message="ok")

    async def _fetch_user_login_info_from_upstream(self, token: str) -> dict:
        url = f"{self._member_api_base}/userLoginInfo.json"
        headers = {
            "app-token": token,
            "source": parse_jwt_source(token),
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json={}, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning("resolve user_id upstream timeout")
            raise TokenIdentityTimeoutError("上游服务响应超时") from exc
        except httpx.RequestError as exc:
            logger.warning("resolve user_id request failed: %s", exc)
            raise TokenIdentityRequestError(f"代理请求失败: {exc}") from exc

        try:
            return response.json()
        except ValueError as exc:
            logger.warning("resolve user_id failed: upstream returned non-json")
            raise TokenIdentityResponseError("上游服务响应格式异常") from exc


token_identity_service = TokenIdentityService()
