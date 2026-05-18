"""Deferred tool state store."""

import logging
from pathlib import Path
from typing import Any, Dict, Optional
import json

from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from app.agent.memory.redis_support import RedisStoreSupport
from app.agent.memory.storage_keys import build_local_json_filename


logger = logging.getLogger(__name__)


class DeferredState(BaseModel):
    tool_call_id: str
    tool_name: str
    message_history_json: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class DeferredStateStore:
    def __init__(
        self,
        base_dir: str = ".data/deferred",
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

    def _redis_key(self, session_id: str, tool_call_id: str) -> str:
        return self._redis.build_key("deferred_state", session_id, tool_call_id)

    def _file_path(self, session_id: str, tool_call_id: str) -> Path:
        return self._base_dir / build_local_json_filename(session_id, tool_call_id)

    def save(self, session_id: str, state: DeferredState) -> bool:
        client = self._redis.get_client()
        payload = state.model_dump_json()
        if client is not None:
            try:
                client.set(self._redis_key(session_id, state.tool_call_id), payload, ex=self._ttl_seconds)
                return True
            except RedisError:
                logger.warning("Redis deferred-state save failed, falling back to local file store.", exc_info=True)

        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._file_path(session_id, state.tool_call_id)
        path.write_text(payload, encoding="utf-8")
        return True

    def load(self, session_id: str, tool_call_id: str) -> Optional[DeferredState]:
        client = self._redis.get_client()
        if client is not None:
            try:
                payload = client.get(self._redis_key(session_id, tool_call_id))
            except RedisError:
                logger.warning("Redis deferred-state load failed, falling back to local file store.", exc_info=True)
            else:
                if payload is None:
                    return None
                return DeferredState.model_validate(json.loads(payload))

        path = self._file_path(session_id, tool_call_id)
        if not path.exists():
            return None
        return DeferredState.model_validate(json.loads(path.read_text(encoding="utf-8")))
