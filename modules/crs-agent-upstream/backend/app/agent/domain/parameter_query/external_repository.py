"""External read-only repository for parameter-query knowledge."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import create_engine, text

from app.agent.domain.parameter_query.models import ExternalKnowledgeSource
from app.core.config import settings


class ExternalParameterKnowledgeRepository:
    """Read-only access to decoder_sit.ai_knowledge pin_info data."""

    def __init__(self) -> None:
        timeout = max(int(settings.param_query_external_mysql_timeout_seconds), 1)
        self._engine = create_engine(
            settings.param_query_external_mysql_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=2,
            max_overflow=2,
            connect_args={
                "connect_timeout": timeout,
                "read_timeout": timeout,
                "write_timeout": timeout,
            },
        )

    def list_pin_info_sources(self) -> list[ExternalKnowledgeSource]:
        stmt = text(
            """
            SELECT
              k.id AS source_knowledge_id,
              k.type_id AS source_type_id,
              t.type_code AS source_type_code,
              k.title,
              k.content_format,
              k.content_summary,
              k.status AS source_status,
              k.is_deleted AS source_is_deleted,
              k.latest_version,
              k.published_version,
              k.update_time
            FROM ai_knowledge k
            JOIN ai_knowledge_type t ON t.id = k.type_id
            WHERE t.type_code = 'pin_info'
            ORDER BY k.update_time DESC, k.id DESC
            """
        )
        with self._engine.connect() as connection:
            rows = connection.execute(stmt).mappings().all()
        return [
            ExternalKnowledgeSource(
                source_knowledge_id=int(row["source_knowledge_id"]),
                source_type_id=int(row["source_type_id"]),
                source_type_code=str(row["source_type_code"]),
                title=str(row["title"] or ""),
                content_format=str(row["content_format"] or "text"),
                content_summary=row["content_summary"],
                source_status=bool(row["source_status"]),
                source_is_deleted=bool(row["source_is_deleted"]),
                latest_version=int(row["latest_version"] or 1),
                published_version=int(row["published_version"]) if row["published_version"] is not None else None,
                update_time=row["update_time"],
            )
            for row in rows
        ]

    def fetch_contents(self, source_ids: Sequence[int]) -> dict[int, str]:
        source_ids = [int(item) for item in source_ids if item]
        if not source_ids:
            return {}

        params = {f"id_{index}": source_id for index, source_id in enumerate(source_ids)}
        placeholders = ", ".join(f":id_{index}" for index in range(len(source_ids)))
        stmt = text(
            f"""
            SELECT id, content
            FROM ai_knowledge
            WHERE id IN ({placeholders})
            """
        )
        with self._engine.connect() as connection:
            rows = connection.execute(stmt, params).mappings().all()
        return {int(row["id"]): str(row["content"] or "") for row in rows}

