"""Configuration helpers for circuit-diagram body search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import URL

from app.core.config import Settings, settings as app_settings


@dataclass(frozen=True)
class CircuitBodySearchConfig:
    enabled: bool
    search_url: str
    search_timeout: float
    pg_host: str
    pg_port: int
    pg_database: str
    pg_user: str
    pg_password: str
    pg_connect_timeout: int
    preview_token_ttl_seconds: int
    preview_result_base_dir: str
    preview_pdf_timeout: float

    @property
    def parsed_db_configured(self) -> bool:
        return all(
            [
                self.pg_host.strip(),
                self.pg_database.strip(),
                self.pg_user.strip(),
                self.pg_password.strip(),
            ]
        )

    def sqlalchemy_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.pg_user,
            password=self.pg_password,
            host=self.pg_host,
            port=self.pg_port,
            database=self.pg_database,
        )


class CircuitBodySearchConfigProvider:
    """Read circuit-body-search config from hot config with settings fallback."""

    def __init__(
        self,
        *,
        config_service: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._config_service = config_service
        self._settings = settings or app_settings

    def load(self) -> CircuitBodySearchConfig:
        return CircuitBodySearchConfig(
            enabled=self._get_bool("circuit_diagram_body_search_enabled", True),
            search_url=self._get_str("circuit_diagram_body_search_url", ""),
            search_timeout=max(self._get_float("circuit_diagram_body_search_timeout", 10.0), 0.1),
            pg_host=self._get_str("circuit_diagram_body_search_pg_host", ""),
            pg_port=max(self._get_int("circuit_diagram_body_search_pg_port", 5432), 1),
            pg_database=self._get_str("circuit_diagram_body_search_pg_database", ""),
            pg_user=self._get_str("circuit_diagram_body_search_pg_user", ""),
            pg_password=self._get_str("circuit_diagram_body_search_pg_password", ""),
            pg_connect_timeout=max(self._get_int("circuit_diagram_body_search_pg_connect_timeout", 5), 1),
            preview_token_ttl_seconds=max(
                self._get_int("circuit_diagram_body_preview_token_ttl_seconds", 86400),
                60,
            ),
            preview_result_base_dir=self._get_str("circuit_diagram_body_preview_result_base_dir", ""),
            preview_pdf_timeout=max(self._get_float("circuit_diagram_body_preview_pdf_timeout", 15.0), 0.1),
        )

    def _get(self, key: str, default: Any) -> Any:
        if self._config_service is not None:
            try:
                return self._config_service.get(key, getattr(self._settings, key, default))
            except Exception:
                return getattr(self._settings, key, default)
        return getattr(self._settings, key, default)

    def _get_str(self, key: str, default: str) -> str:
        value = self._get(key, default)
        normalized = str(value or "").strip()
        if normalized:
            return normalized
        fallback = str(getattr(self._settings, key, default) or "").strip()
        return fallback

    def _get_int(self, key: str, default: int) -> int:
        try:
            return int(self._get(key, default))
        except (TypeError, ValueError):
            return default

    def _get_float(self, key: str, default: float) -> float:
        try:
            return float(self._get(key, default))
        except (TypeError, ValueError):
            return default

    def _get_bool(self, key: str, default: bool) -> bool:
        value = self._get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "on"}
