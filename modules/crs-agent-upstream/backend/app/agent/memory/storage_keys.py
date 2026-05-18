"""Safe key helpers for local fallback stores."""

from __future__ import annotations

import hashlib
import re


_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def encode_local_key(raw: str) -> str:
    """Keep simple ids readable and hash everything else for safe filenames."""
    text = str(raw or "")
    if _SAFE_KEY_RE.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256_{digest}"


def build_local_json_filename(*parts: str) -> str:
    encoded_parts = [encode_local_key(part) for part in parts]
    return f"{'__'.join(encoded_parts)}.json"
