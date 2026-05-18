"""FastAPI application entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.service import AgentLoopService
from app.api.admin_auth import router as admin_auth_router
from app.api.admin_benchmark import router as admin_benchmark_router
from app.api.admin_config import router as admin_config_router
from app.api.admin_dashboard import router as admin_dashboard_router
from app.api.admin_dimension import router as admin_dimension_router
from app.api.admin_feedback import router as admin_feedback_router
from app.api.admin_logs import router as admin_logs_router
from app.api.chat import router as chat_router
from app.api.feedback import router as feedback_router
from app.api.ggzj import router as ggzj_router
from app.api.health import router as health_router
from app.api.image import router as image_router
from app.api.legacy_proxy import router as legacy_proxy_router
from app.api.search import router as search_router
from app.api.speech import router as speech_router
from app.core.config import settings


logger = logging.getLogger(__name__)


def _include_compat_router(app: FastAPI, router) -> None:
    app.include_router(router)
    app.include_router(router, prefix="/chat/api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    deps = AgentRuntimeDeps.build_default()
    app.state.runtime_deps = deps
    background_tasks: list[asyncio.Task] = []

    async def run_oss_image_delete_worker(worker_service) -> None:
        while True:
            try:
                result = await worker_service.process_due_jobs_once()
                if result.get("processed"):
                    logger.info(
                        "OSS image delete worker processed=%s confirmed=%s failed=%s",
                        result.get("processed"),
                        result.get("confirmed"),
                        result.get("failed"),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("OSS image delete worker failed: %s", exc)
            await asyncio.sleep(worker_service.worker_interval_seconds)

    try:
        from app.legacy.services.dimension_service import dimension_service
        from app.legacy.services.config_initializer import reconcile_system_configs

        if deps.db_session_factory is not None:
            db = deps.db_session_factory()
            try:
                reconcile_result = reconcile_system_configs(db)
                if deps.config_service is not None:
                    deps.config_service.refresh()
                if any(
                    reconcile_result[key] > 0
                    for key in ("created_count", "deleted_count", "updated_meta_count", "updated_value_count")
                ):
                    logger.info(
                        "System configs reconciled. created=%s deleted=%s updated_meta=%s updated_values=%s",
                        reconcile_result["created_count"],
                        reconcile_result["deleted_count"],
                        reconcile_result["updated_meta_count"],
                        reconcile_result["updated_value_count"],
                    )
                dimension_service.load(db)
            finally:
                db.close()
    except Exception as exc:
        logger.warning(
            "Legacy bootstrap warmup failed; runtime will continue with partial deps. reason=%s",
            exc,
        )

    try:
        parameter_query_service = getattr(deps, "parameter_query_service", None)
        if parameter_query_service is not None:
            local_status = parameter_query_service.ensure_local_index()
            logger.info(
                "Parameter query local index loaded. sources=%s rows=%s",
                local_status.get("source_count"),
                local_status.get("row_count"),
            )

            has_local_cache = bool(parameter_query_service.index_store and parameter_query_service.index_store.has_data())
            param_query_enabled = bool(
                deps.config_service.get("param_query_enabled", settings.param_query_enabled)
                if deps.config_service is not None
                else settings.param_query_enabled
            )
            param_query_sync_on_startup = bool(
                deps.config_service.get("param_query_sync_on_startup", settings.param_query_sync_on_startup)
                if deps.config_service is not None
                else settings.param_query_sync_on_startup
            )
            if param_query_enabled and param_query_sync_on_startup:
                if has_local_cache:
                    background_tasks.append(
                        asyncio.create_task(
                            asyncio.to_thread(
                                parameter_query_service.sync_now,
                                job_type="startup_sync",
                            )
                        )
                    )
                else:
                    parameter_query_service.sync_now(job_type="startup_sync")
    except Exception as exc:
        logger.warning(
            "Parameter query warmup failed; runtime will continue with cached or empty data. reason=%s",
            exc,
        )

    try:
        if deps.db_session_factory is not None:
            from app.legacy.services.oss_image_delete_service import OssImageDeleteService

            oss_delete_service = OssImageDeleteService(
                session_factory=deps.db_session_factory,
                config_service=deps.config_service,
            )
            oss_delete_service.ensure_schema()
            if oss_delete_service.enabled:
                app.state.oss_image_delete_service = oss_delete_service
                background_tasks.append(asyncio.create_task(run_oss_image_delete_worker(oss_delete_service)))
    except Exception as exc:
        logger.warning("OSS image delete worker bootstrap failed; runtime will continue. reason=%s", exc)

    app.state.db_session_factory = deps.db_session_factory
    app.state.agent_service = AgentLoopService(deps=deps)

    yield

    for task in background_tasks:
        if not task.done():
            task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(
        title="CRS Agent",
        version="0.1.0",
        description="New Agent Loop based backend skeleton.",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(search_router)
    app.include_router(speech_router)
    app.include_router(ggzj_router)
    app.include_router(image_router)
    app.include_router(legacy_proxy_router)
    _include_compat_router(app, feedback_router)
    app.include_router(admin_auth_router)
    _include_compat_router(app, admin_benchmark_router)
    _include_compat_router(app, admin_dashboard_router)
    _include_compat_router(app, admin_config_router)
    _include_compat_router(app, admin_dimension_router)
    _include_compat_router(app, admin_logs_router)
    _include_compat_router(app, admin_feedback_router)
    return app


app = create_app()
