"""Shared Redis bootstrap for agent memory stores."""

from pathlib import Path
from typing import Any

from redis import Redis


class RedisStoreSupport:
    """Lazy Redis client holder used by short-term memory stores."""

    def __init__(
        self,
        redis_url: str | None = None,
        key_prefix: str = "crs_agent",
        client: Any | None = None,
    ):
        self._redis_url = redis_url or None
        self._key_prefix = key_prefix.strip(":")
        self._client = client

    def build_key(self, *parts: str) -> str:
        suffix = ":".join(str(part).strip(":") for part in parts if part not in ("", None))
        if not self._key_prefix:
            return suffix
        return f"{self._key_prefix}:{suffix}"

    def get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if not self._redis_url:
            return None

        self._client = Redis.from_url(self._redis_url, decode_responses=True)
        return self._client
