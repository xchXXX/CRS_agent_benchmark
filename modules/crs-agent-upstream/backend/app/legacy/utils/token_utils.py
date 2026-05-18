"""JWT token helper functions."""

import base64
import json
import logging


logger = logging.getLogger(__name__)


def parse_jwt_source(token: str, default: str = "APP") -> str:
    """Parse the upstream source value from a JWT aud claim."""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            aud = payload.get("aud")
            if aud:
                return str(aud)
    except Exception as exc:
        logger.warning("[token_utils] JWT aud parse failed, fallback source=%s: %s", default, exc)
    return default
