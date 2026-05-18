"""Shared case context store."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from redis.exceptions import RedisError

from app.agent.context.models import CaseContext
from app.agent.memory.redis_support import RedisStoreSupport
from app.agent.memory.storage_keys import build_local_json_filename


logger = logging.getLogger(__name__)


class CaseContextStore:
    """Persist shared case context in Redis with local file fallback."""

    def __init__(
        self,
        base_dir: str = ".data/case_context",
        redis_url: str | None = None,
        redis_key_prefix: str = "crs_agent",
        ttl_seconds: int | None = None,
        redis_client=None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._ttl_seconds = ttl_seconds
        self._redis = RedisStoreSupport(
            redis_url=redis_url,
            key_prefix=redis_key_prefix,
            client=redis_client,
        )

    def _redis_key(self, session_id: str) -> str:
        return self._redis.build_key("case_context", session_id)

    def _file_path(self, session_id: str) -> Path:
        return self._base_dir / build_local_json_filename(session_id)

    def load(self, session_id: str) -> CaseContext | None:
        client = self._redis.get_client()
        if client is not None:
            try:
                payload = client.get(self._redis_key(session_id))
            except RedisError:
                logger.warning("Redis case-context load failed, falling back to local file store.", exc_info=True)
            else:
                if payload is None:
                    return None
                return CaseContext.model_validate(json.loads(payload))

        path = self._file_path(session_id)
        if not path.exists():
            return None
        return CaseContext.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, context: CaseContext) -> bool:
        payload = context.model_dump_json()
        client = self._redis.get_client()
        if client is not None:
            try:
                client.set(self._redis_key(context.session_id), payload, ex=self._ttl_seconds)
                return True
            except RedisError:
                logger.warning("Redis case-context save failed, falling back to local file store.", exc_info=True)

        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._file_path(context.session_id)
        path.write_text(payload, encoding="utf-8")
        return True

    def clear(self, session_id: str) -> bool:
        cleared = False
        client = self._redis.get_client()
        if client is not None:
            try:
                cleared = bool(client.delete(self._redis_key(session_id))) or cleared
            except RedisError:
                logger.warning("Redis case-context clear failed, falling back to local file store.", exc_info=True)

        path = self._file_path(session_id)
        if path.exists():
            path.unlink()
            cleared = True
        return cleared
