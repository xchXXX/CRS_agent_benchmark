"""Runtime dependency container."""

from dataclasses import dataclass, replace
from typing import Any

from app.agent.context.store import CaseContextStore
from app.agent.memory.doc_search_cache_store import DocSearchCacheStore
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.tools.registry import ToolRegistry, build_default_tool_registry
from app.core.config import settings


@dataclass
class AgentRuntimeDeps:
    tool_registry: ToolRegistry
    message_history_store: MessageHistoryStore
    deferred_state_store: DeferredStateStore
    mem0_store: Mem0Store
    tracer: LoopTracer
    case_context_store: CaseContextStore | Any = None
    db_session_factory: Any = None
    search_engine_factory: Any = None
    clarify_service: Any = None
    config_service: Any = None
    dimension_service: Any = None
    existence_validator: Any = None
    hard_constraint_validator: Any = None
    llm_clarify_service: Any = None
    doc_search_cache_store: DocSearchCacheStore | Any = None
    token_identity_service: Any = None
    ggzj_search_client: Any = None
    ggzj_result_adapter: Any = None
    ggzj_file_url_resolver: Any = None
    diagnosis_client: Any = None
    ecu_service: Any = None
    fault_code_parser: Any = None
    repair_knowledge_service: Any = None
    parameter_query_service: Any = None
    circuit_body_search_enhancer: Any = None
    circuit_body_search_client: Any = None
    circuit_body_preview_token_codec: Any = None
    circuit_body_preview_renderer: Any = None
    app_token: str | None = None
    user_id: int | None = None
    request_session_id: str | None = None
    enforce_external_doc_search: bool = False
    case_context: Any = None
    loop_guard: Any = None
    runtime_tool_history: list[dict[str, Any]] | None = None
    llm_observability: dict[str, Any] | None = None

    def clone_for_request(self, **overrides: Any) -> "AgentRuntimeDeps":
        return replace(self, **overrides)

    @classmethod
    def build_default(cls) -> "AgentRuntimeDeps":
        deps = cls(
            tool_registry=build_default_tool_registry(),
            message_history_store=MessageHistoryStore(
                redis_url=settings.redis_url,
                redis_key_prefix=settings.redis_key_prefix,
                ttl_seconds=settings.message_history_ttl_seconds,
            ),
            deferred_state_store=DeferredStateStore(
                redis_url=settings.redis_url,
                redis_key_prefix=settings.redis_key_prefix,
                ttl_seconds=settings.deferred_state_ttl_seconds,
            ),
            mem0_store=Mem0Store(enabled=settings.mem0_enabled),
            tracer=LoopTracer(),
            case_context_store=CaseContextStore(
                redis_url=settings.redis_url,
                redis_key_prefix=settings.redis_key_prefix,
                ttl_seconds=settings.case_context_ttl_seconds,
            ),
            doc_search_cache_store=DocSearchCacheStore(
                redis_url=settings.redis_url,
                redis_key_prefix=settings.redis_key_prefix,
                ttl_seconds=settings.doc_search_external_cache_ttl_seconds,
            ),
        )
        try:
            from app.agent.domain.repair_knowledge import RepairKnowledgeService

            deps.repair_knowledge_service = RepairKnowledgeService(settings.repair_knowledge_path)
        except Exception as exc:
            deps.tracer.trace(
                event_type="repair_knowledge_bootstrap_unavailable",
                session_id=None,
                detail=str(exc),
            )
        try:
            from app.agent.domain.doc_search.llm_smart import PydanticAIDocSearchLLMClarifyService
            from app.agent.domain.circuit_body_search import (
                CircuitBodyPreviewRenderer,
                CircuitBodyPreviewTokenCodec,
                CircuitBodySearchEnhancer,
                PydanticAICircuitBodyHitReranker,
            )
            from app.agent.domain.circuit_body_search.config import CircuitBodySearchConfigProvider
            from app.agent.domain.circuit_body_search.search_client import CircuitBodySearchClient
            from app.legacy.models.database import get_session_local
            from app.legacy.services.clarify_service import ClarifyService
            from app.legacy.services.config_service import config_service
            from app.legacy.services.diagnosis import (
                get_diagnosis_client,
                get_ecu_service,
                get_fault_code_parser,
            )
            from app.legacy.services.dimension_service import dimension_service
            from app.legacy.services.existence_validator import ExistenceValidator
            from app.legacy.services.ggzj import GgzjFileUrlResolver, GgzjResultAdapter, GgzjSearchClient
            from app.legacy.services.hard_constraint_validator import hard_constraint_validator
            from app.legacy.services.search_engine import SearchEngine
            from app.legacy.services.token_identity_service import token_identity_service

            deps.db_session_factory = get_session_local()
            deps.config_service = config_service
            deps.dimension_service = dimension_service
            deps.search_engine_factory = SearchEngine
            deps.clarify_service = ClarifyService()
            deps.existence_validator = ExistenceValidator()
            deps.hard_constraint_validator = hard_constraint_validator
            deps.llm_clarify_service = PydanticAIDocSearchLLMClarifyService(config_service=config_service)
            deps.token_identity_service = token_identity_service
            deps.ggzj_search_client = GgzjSearchClient()
            deps.ggzj_result_adapter = GgzjResultAdapter()
            deps.ggzj_file_url_resolver = GgzjFileUrlResolver()
            deps.diagnosis_client = get_diagnosis_client()
            deps.ecu_service = get_ecu_service()
            deps.fault_code_parser = get_fault_code_parser()
            circuit_body_config_provider = CircuitBodySearchConfigProvider(config_service=config_service)
            deps.circuit_body_preview_token_codec = CircuitBodyPreviewTokenCodec()
            deps.circuit_body_preview_renderer = CircuitBodyPreviewRenderer(
                config_provider=circuit_body_config_provider,
            )
            deps.circuit_body_search_client = CircuitBodySearchClient(
                config_provider=circuit_body_config_provider,
            )
            deps.circuit_body_search_enhancer = CircuitBodySearchEnhancer(
                config_service=config_service,
                config_provider=circuit_body_config_provider,
                search_client=deps.circuit_body_search_client,
                hit_reranker=PydanticAICircuitBodyHitReranker(config_service=config_service),
                preview_token_codec=deps.circuit_body_preview_token_codec,
            )
        except Exception:
            deps.tracer.trace(
                event_type="legacy_bootstrap_unavailable",
                session_id=None,
                detail="legacy db/config bootstrap not ready",
            )
        if deps.db_session_factory is not None:
            try:
                from app.agent.domain.parameter_query import ParameterQueryService

                deps.parameter_query_service = ParameterQueryService(
                    session_factory=deps.db_session_factory,
                    config_service=deps.config_service,
                )
            except Exception as exc:
                deps.tracer.trace(
                    event_type="parameter_query_bootstrap_unavailable",
                    session_id=None,
                    detail=str(exc),
                )
        return deps
