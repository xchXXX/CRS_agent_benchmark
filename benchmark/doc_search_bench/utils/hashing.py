from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any


def _to_primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_primitive(item) for item in value]
    return value


def stable_hash(payload: Any) -> str:
    body = json.dumps(_to_primitive(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(body).hexdigest()
