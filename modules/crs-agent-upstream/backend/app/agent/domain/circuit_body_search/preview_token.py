"""Signed tokens for circuit body-search preview images."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Mapping

from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings


DEFAULT_PREVIEW_TOKEN_TTL_SECONDS = 24 * 60 * 60


class CircuitBodyPreviewTokenError(ValueError):
    """Raised when a preview token cannot be trusted."""


class CircuitBodyPreviewTokenPayload(BaseModel):
    """Trusted parameters needed to render one document hit preview."""

    pdf_id: str = ""
    filename: str = ""
    keyword: str = ""
    hit_id: str = ""
    latest_result_path: str = ""
    source_pdf_url: str = ""
    page_index: int
    highlight_boxes_px: list[list[float]] = Field(default_factory=list)
    iat: int = 0
    exp: int = 0


class CircuitBodyPreviewTokenCodec:
    """HMAC codec that keeps raw parser paths out of unsigned frontend state."""

    def __init__(self, *, secret: str | None = None) -> None:
        self._secret = str(secret or settings.jwt_secret_key or settings.app_name).encode("utf-8")

    def encode(
        self,
        payload: Mapping[str, Any],
        *,
        ttl_seconds: int = DEFAULT_PREVIEW_TOKEN_TTL_SECONDS,
    ) -> str:
        now = int(time.time())
        body = dict(payload)
        body["iat"] = int(body.get("iat") or now)
        body["exp"] = int(body.get("exp") or (now + max(int(ttl_seconds or 0), 60)))
        normalized = CircuitBodyPreviewTokenPayload.model_validate(body)
        raw = json.dumps(
            normalized.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded_body = self._b64encode(raw)
        signature = self._signature(encoded_body)
        return f"{encoded_body}.{signature}"

    def decode(self, token: str) -> CircuitBodyPreviewTokenPayload:
        try:
            encoded_body, signature = str(token or "").split(".", 1)
        except ValueError as exc:
            raise CircuitBodyPreviewTokenError("invalid_preview_token") from exc

        expected_signature = self._signature(encoded_body)
        if not hmac.compare_digest(signature, expected_signature):
            raise CircuitBodyPreviewTokenError("invalid_preview_token_signature")

        try:
            raw = self._b64decode(encoded_body)
            data = json.loads(raw.decode("utf-8"))
            payload = CircuitBodyPreviewTokenPayload.model_validate(data)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError) as exc:
            raise CircuitBodyPreviewTokenError("invalid_preview_token_payload") from exc

        if payload.exp and payload.exp < int(time.time()):
            raise CircuitBodyPreviewTokenError("preview_token_expired")
        return payload

    def _signature(self, encoded_body: str) -> str:
        digest = hmac.new(self._secret, encoded_body.encode("utf-8"), hashlib.sha256).digest()
        return self._b64encode(digest)

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
