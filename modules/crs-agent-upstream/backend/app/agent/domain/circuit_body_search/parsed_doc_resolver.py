"""Resolve doc-search filenames to parsed circuit-diagram PDF ids."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.agent.domain.circuit_body_search.config import CircuitBodySearchConfigProvider
from app.agent.domain.circuit_body_search.models import ParsedCircuitDocument


logger = logging.getLogger(__name__)


_COMPACT_SEPARATORS_RE = re.compile(r"[_\s\-\.,;:!?/\\()（）【】\[\]{}]+")
_SQL_COMPACT_PATTERN = r"[_\s\-\.,;:!?/\\()（）【】\[\]{}]+"


def normalize_circuit_filename(value: Any) -> str:
    filename = str(value or "").strip()
    filename = re.sub(r"\.(pdf|PDF)$", "", filename)
    return filename


def compact_circuit_text(value: Any) -> str:
    return _COMPACT_SEPARATORS_RE.sub("", normalize_circuit_filename(value)).lower()


def build_circuit_query_terms(value: Any) -> list[str]:
    compact = compact_circuit_text(value)
    if not compact:
        return []
    if len(compact) <= 2:
        return [compact]
    return list(dict.fromkeys(compact[index : index + 2] for index in range(len(compact) - 1)))


class ParsedCircuitDocResolver:
    """Read-only resolver against the parsed circuit-diagram PostgreSQL DB."""

    def __init__(
        self,
        *,
        config_provider: CircuitBodySearchConfigProvider,
        engine: Engine | None = None,
    ) -> None:
        self._config_provider = config_provider
        self._engine = engine
        self._engine_signature: tuple[Any, ...] | None = None

    def resolve_many(self, filenames: Iterable[str]) -> dict[str, ParsedCircuitDocument]:
        normalized = [normalize_circuit_filename(item) for item in filenames]
        unique_names = list(dict.fromkeys(name for name in normalized if name))
        if not unique_names:
            return {}

        config = self._config_provider.load()
        if not config.enabled or not config.parsed_db_configured:
            return {}

        engine = self._get_engine()
        params = {f"name_{index}": name for index, name in enumerate(unique_names)}
        placeholders = ", ".join(f":name_{index}" for index in range(len(unique_names)))
        stmt = text(
            f"""
            WITH ranked AS (
              SELECT
                item_id,
                name,
                url_raw_sample,
                latest_pdf_id,
                latest_result_path,
                updated_at,
                ROW_NUMBER() OVER (
                  PARTITION BY name
                  ORDER BY updated_at DESC NULLS LAST
                ) AS rn
              FROM items
              WHERE name IN ({placeholders})
                AND latest_status = 'completed'
                AND latest_pdf_id <> ''
                AND latest_result_path <> ''
            )
            SELECT
              item_id,
              name,
              url_raw_sample,
              latest_pdf_id,
              latest_result_path,
              updated_at
            FROM ranked
            WHERE rn = 1
            """
        )

        try:
            with engine.connect() as connection:
                rows = connection.execute(stmt, params).mappings().all()
        except Exception as exc:
            logger.warning("Circuit parsed-document resolve failed: %s", exc)
            return {}

        resolved: dict[str, ParsedCircuitDocument] = {}
        for row in rows:
            name = normalize_circuit_filename(row.get("name"))
            if not name:
                continue
            resolved[name] = ParsedCircuitDocument(
                item_id=str(row.get("item_id") or ""),
                name=str(row.get("name") or ""),
                latest_pdf_id=str(row.get("latest_pdf_id") or ""),
                latest_result_path=str(row.get("latest_result_path") or ""),
                url_raw_sample=str(row.get("url_raw_sample") or ""),
                updated_at=row.get("updated_at"),
            )
        return resolved

    def search_candidates(self, query: str, *, limit: int = 20) -> list[ParsedCircuitDocument]:
        query_compact = compact_circuit_text(query)
        terms = build_circuit_query_terms(query)
        if not query_compact or not terms or limit <= 0:
            return []

        config = self._config_provider.load()
        if not config.enabled or not config.parsed_db_configured:
            return []

        engine = self._get_engine()
        fetch_limit = min(max(limit * 20, 120), 400)
        params: dict[str, Any] = {
            "compact_pattern": _SQL_COMPACT_PATTERN,
            "fetch_limit": fetch_limit,
        }
        for index, term in enumerate(terms):
            params[f"term_{index}"] = f"%{term}%"
        compact_name_expr = "regexp_replace(lower(name), :compact_pattern, '', 'g')"
        term_clause = " OR ".join(f"{compact_name_expr} LIKE :term_{index}" for index in range(len(terms)))
        stmt = text(
            f"""
            SELECT
              item_id,
              name,
              url_raw_sample,
              latest_pdf_id,
              latest_result_path,
              updated_at
            FROM items
            WHERE latest_status = 'completed'
              AND latest_pdf_id <> ''
              AND latest_result_path <> ''
              AND ({term_clause})
            ORDER BY updated_at DESC NULLS LAST
            LIMIT :fetch_limit
            """
        )

        try:
            with engine.connect() as connection:
                rows = connection.execute(stmt, params).mappings().all()
        except Exception as exc:
            logger.warning("Circuit parsed-document candidate search failed: %s", exc)
            return []

        minimum_score = 0.5 if len(terms) >= 4 else 0.75
        ranked: list[tuple[float, ParsedCircuitDocument]] = []
        for row in rows:
            name = normalize_circuit_filename(row.get("name"))
            score = self._candidate_score(name, query_compact=query_compact, terms=terms)
            if score < minimum_score:
                continue
            ranked.append(
                (
                    score,
                    ParsedCircuitDocument(
                        item_id=str(row.get("item_id") or ""),
                        name=str(row.get("name") or ""),
                        latest_pdf_id=str(row.get("latest_pdf_id") or ""),
                        latest_result_path=str(row.get("latest_result_path") or ""),
                        url_raw_sample=str(row.get("url_raw_sample") or ""),
                        updated_at=row.get("updated_at"),
                    ),
                )
            )

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in ranked[:limit]]

    @staticmethod
    def _candidate_score(name: str, *, query_compact: str, terms: list[str]) -> float:
        compact_name = compact_circuit_text(name)
        if not compact_name:
            return 0.0
        term_hits = sum(1 for term in terms if term in compact_name)
        term_score = term_hits / max(len(terms), 1)
        exact_bonus = 2.0 if query_compact and query_compact in compact_name else 0.0
        length_penalty = min(len(compact_name) / 10000, 0.05)
        return exact_bonus + term_score - length_penalty

    def _get_engine(self) -> Engine:
        config = self._config_provider.load()
        signature = (
            config.pg_host,
            config.pg_port,
            config.pg_database,
            config.pg_user,
            config.pg_password,
            config.pg_connect_timeout,
        )
        if self._engine is not None and (self._engine_signature is None or self._engine_signature == signature):
            return self._engine

        self._engine = create_engine(
            config.sqlalchemy_url(),
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=2,
            max_overflow=2,
            connect_args={
                "connect_timeout": config.pg_connect_timeout,
                "options": "-c default_transaction_read_only=on",
            },
        )
        self._engine_signature = signature
        return self._engine
