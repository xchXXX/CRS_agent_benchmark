"""Short-lived cache store for external doc_search source payloads."""

import json
import logging
from pathlib import Path
from typing import Any

from redis.exceptions import RedisError

from app.agent.memory.redis_support import RedisStoreSupport
from app.agent.memory.storage_keys import build_local_json_filename


logger = logging.getLogger(__name__)


class DocSearchCacheStore:
    """Store cached external-search payloads in Redis with file fallback."""

    def __init__(
        self,
        base_dir: str = ".data/doc_search_cache",
        redis_url: str | None = None,
        redis_key_prefix: str = "crs_agent",
        ttl_seconds: int | None = None,
        redis_client=None,
    ):
        self._base_dir = Path(base_dir)
        self._ttl_seconds = ttl_seconds
        self._redis = RedisStoreSupport(
            redis_url=redis_url,
            key_prefix=redis_key_prefix,
            client=redis_client,
        )

    def _redis_key(self, cache_key: str) -> str:
        return self._redis.build_key("doc_search_cache", cache_key)

    def _file_path(self, cache_key: str) -> Path:
        return self._base_dir / build_local_json_filename(cache_key)

    def load(self, cache_key: str) -> dict[str, Any] | None:
        client = self._redis.get_client()
        if client is not None:
            try:
                payload = client.get(self._redis_key(cache_key))
            except RedisError:
                logger.warning("Redis doc-search cache load failed, falling back to local file store.", exc_info=True)
            else:
                if payload is None:
                    return None
                return json.loads(payload)

        path = self._file_path(cache_key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, cache_key: str, payload: dict[str, Any]) -> bool:
        serialized = json.dumps(payload, ensure_ascii=False)
        client = self._redis.get_client()
        if client is not None:
            try:
                client.set(self._redis_key(cache_key), serialized, ex=self._ttl_seconds)
                return True
            except RedisError:
                logger.warning("Redis doc-search cache save failed, falling back to local file store.", exc_info=True)

        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._file_path(cache_key)
        path.write_text(serialized, encoding="utf-8")
        return True
