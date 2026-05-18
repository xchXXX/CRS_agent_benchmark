"""Short-term conversation history store."""

import logging
from pathlib import Path
from typing import Optional

from redis.exceptions import RedisError

from app.agent.memory.redis_support import RedisStoreSupport
from app.agent.memory.storage_keys import build_local_json_filename


logger = logging.getLogger(__name__)


class MessageHistoryStore:
    """Store serialized Pydantic AI message history in Redis, with file fallback."""

    def __init__(
        self,
        base_dir: str = ".data/message_history",
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

    def _redis_key(self, session_id: str) -> str:
        return self._redis.build_key("message_history", session_id)

    def _file_path(self, session_id: str) -> Path:
        return self._base_dir / build_local_json_filename(session_id)

    def load_serialized_history(self, session_id: str) -> Optional[str]:
        client = self._redis.get_client()
        if client is not None:
            try:
                return client.get(self._redis_key(session_id))
            except RedisError:
                logger.warning("Redis history load failed, falling back to local file store.", exc_info=True)

        path = self._file_path(session_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def save_serialized_history(self, session_id: str, payload: str) -> bool:
        client = self._redis.get_client()
        if client is not None:
            try:
                client.set(self._redis_key(session_id), payload, ex=self._ttl_seconds)
                return True
            except RedisError:
                logger.warning("Redis history save failed, falling back to local file store.", exc_info=True)

        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._file_path(session_id)
        path.write_text(payload, encoding="utf-8")
        return True
