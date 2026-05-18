"""Chat endpoints."""

import json
from time import perf_counter

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.agent.domain.image_evidence import ImageEvidenceRequest, ImageEvidenceService
from app.agent.adapters.frontend_protocol import FrontendProtocolAdapter
from app.agent.observability.task_log_service import AgentTaskLogService
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.service import AgentLoopService
from app.agent.models.events import AgentEventType
from app.api.frontend_visibility import (
    is_frontend_source_display_enabled,
    sanitize_agent_event,
    sanitize_chat_response,
)
from app.api.image import _read_image_upload
from app.api.request_context import build_request_runtime_deps
from app.core.config import settings
from app.schemas.chat import ChatRequest, ChatResponse, StreamAbortRequest

router = APIRouter(tags=["chat"])


def _get_agent_service(request: Request) -> AgentLoopService:
    service = getattr(request.app.state, "agent_service", None)
    if service is not None:
        return service

    deps = AgentRuntimeDeps.build_default()
    service = AgentLoopService(deps=deps)
    request.app.state.runtime_deps = deps
    request.app.state.agent_service = service
    return service


def _persist_admin_log(
    *,
    request: ChatRequest,
    response: ChatResponse | None,
    runtime_deps: AgentRuntimeDeps,
    elapsed_ms: int,
    transport: str,
) -> None:
    if response is None or runtime_deps.db_session_factory is None:
        return
    AgentTaskLogService(runtime_deps.db_session_factory).persist_interaction(
        request=request,
        response=response,
        user_id=runtime_deps.user_id,
        trace_entries=runtime_deps.tracer.entries(),
        elapsed_ms=elapsed_ms,
        transport=transport,
    )


def _parse_multipart_chat_request(payload: str) -> ChatRequest:
    try:
        return ChatRequest.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="聊天请求 JSON 格式错误") from exc


async def _attach_image_evidence_to_request(
    *,
    request: ChatRequest,
    images: list[UploadFile] | None,
    runtime_deps: AgentRuntimeDeps,
) -> ChatRequest:
    if not images:
        return request

    max_images = int(settings.image_evidence_max_images)
    if len(images) > max_images:
        raise HTTPException(status_code=400, detail=f"一次最多上传{max_images}张图片")

    image_inputs = [await _read_image_upload(image) for image in images]
    evidence_result = await ImageEvidenceService(config_service=runtime_deps.config_service).analyze(
        ImageEvidenceRequest(images=image_inputs, user_prompt=request.message)
    )
    if not evidence_result.success or evidence_result.evidence is None:
        message = (
            evidence_result.error.get("message")
            if isinstance(evidence_result.error, dict)
            else None
        ) or "图片识别失败"
        raise HTTPException(status_code=400, detail=message)

    context = dict(request.context or {})
    existing = context.get("image_evidences")
    if isinstance(existing, list):
        image_evidences = list(existing)
    elif existing:
        image_evidences = [existing]
    else:
        image_evidences = []
    image_evidences.append(evidence_result.evidence.model_dump(mode="json"))
    context["image_evidences"] = image_evidences
    return request.model_copy(update={"context": context})


@router.post("/chat/completions", response_model=ChatResponse)
@router.post("/chat/api/chat/completions", response_model=ChatResponse)
async def chat_completions(request: ChatRequest, http_request: Request) -> ChatResponse:
    service = _get_agent_service(http_request)
    runtime_deps = await build_request_runtime_deps(http_request)
    started_at = perf_counter()
    response = await service.process(request, runtime_deps=runtime_deps)
    _persist_admin_log(
        request=request,
        response=response,
        runtime_deps=runtime_deps,
        elapsed_ms=max(0, int((perf_counter() - started_at) * 1000)),
        transport="http",
    )
    return sanitize_chat_response(response, runtime_deps)


@router.post("/chat/completions-with-images", response_model=ChatResponse)
@router.post("/chat/api/chat/completions-with-images", response_model=ChatResponse)
async def chat_completions_with_images(
    http_request: Request,
    request_payload: str = Form(..., alias="request"),
    images: list[UploadFile] | None = File(default=None),
) -> ChatResponse:
    service = _get_agent_service(http_request)
    runtime_deps = await build_request_runtime_deps(http_request)
    request = await _attach_image_evidence_to_request(
        request=_parse_multipart_chat_request(request_payload),
        images=images,
        runtime_deps=runtime_deps,
    )
    started_at = perf_counter()
    response = await service.process(request, runtime_deps=runtime_deps)
    _persist_admin_log(
        request=request,
        response=response,
        runtime_deps=runtime_deps,
        elapsed_ms=max(0, int((perf_counter() - started_at) * 1000)),
        transport="http_multipart",
    )
    return sanitize_chat_response(response, runtime_deps)


@router.post("/chat/stream")
@router.post("/chat/api/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    service = _get_agent_service(http_request)
    runtime_deps = await build_request_runtime_deps(http_request)
    adapter = FrontendProtocolAdapter()
    started_at = perf_counter()

    async def generate():
        final_response: ChatResponse | None = None
        async for event in service.stream(request, runtime_deps=runtime_deps):
            if event.type == AgentEventType.DONE:
                payload = event.metadata.get("response")
                if payload:
                    final_response = ChatResponse.model_validate(payload)
            elif event.type == AgentEventType.ERROR:
                final_response = ChatResponse(
                    type="error",
                    content={
                        "message": event.message or "系统处理请求时发生错误，请稍后重试。",
                        "error_code": "STREAM_RUNTIME_ERROR",
                        "reason": event.metadata.get("detail"),
                    },
                    session_id=event.session_id or (request.session_id or ""),
                    request_id=event.metadata.get("request_id"),
                    business="AGENT_LOOP",
                )
            payload = adapter.to_event(sanitize_agent_event(event, runtime_deps))
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        _persist_admin_log(
            request=request,
            response=final_response,
            runtime_deps=runtime_deps,
            elapsed_ms=max(0, int((perf_counter() - started_at) * 1000)),
            transport="stream",
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/stream-with-images")
@router.post("/chat/api/chat/stream-with-images")
async def chat_stream_with_images(
    http_request: Request,
    request_payload: str = Form(..., alias="request"),
    images: list[UploadFile] | None = File(default=None),
) -> StreamingResponse:
    service = _get_agent_service(http_request)
    runtime_deps = await build_request_runtime_deps(http_request)
    request = await _attach_image_evidence_to_request(
        request=_parse_multipart_chat_request(request_payload),
        images=images,
        runtime_deps=runtime_deps,
    )
    adapter = FrontendProtocolAdapter()
    started_at = perf_counter()

    async def generate():
        final_response: ChatResponse | None = None
        async for event in service.stream(request, runtime_deps=runtime_deps):
            if event.type == AgentEventType.DONE:
                payload = event.metadata.get("response")
                if payload:
                    final_response = ChatResponse.model_validate(payload)
            elif event.type == AgentEventType.ERROR:
                final_response = ChatResponse(
                    type="error",
                    content={
                        "message": event.message or "系统处理请求时发生错误，请稍后重试。",
                        "error_code": "STREAM_RUNTIME_ERROR",
                        "reason": event.metadata.get("detail"),
                    },
                    session_id=event.session_id or (request.session_id or ""),
                    request_id=event.metadata.get("request_id"),
                    business="AGENT_LOOP",
                )
            payload = adapter.to_event(sanitize_agent_event(event, runtime_deps))
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        _persist_admin_log(
            request=request,
            response=final_response,
            runtime_deps=runtime_deps,
            elapsed_ms=max(0, int((perf_counter() - started_at) * 1000)),
            transport="stream_multipart",
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/stream/abort")
@router.post("/chat/api/chat/stream/abort")
async def abort_chat_stream(request: StreamAbortRequest, http_request: Request) -> dict:
    await build_request_runtime_deps(http_request)
    service = _get_agent_service(http_request)
    saved = service.handle_stream_abort(request.session_id, request.partial_content)
    if not saved:
        return {"status": "ok", "message": "会话不存在或无内容需要保存"}
    return {"status": "ok", "message": "中断内容已保存"}


@router.get("/chat/health")
@router.get("/chat/api/chat/health")
async def chat_health() -> dict:
    status = AgentFactory().get_status()
    return {
        "status": "ok" if status.available else "degraded",
        "runtime": "pydantic_ai",
        "available": status.available,
        "reason": status.reason,
        "version": status.version,
    }


@router.get("/repair-knowledge/source/{entry_id}")
@router.get("/chat/api/repair-knowledge/source/{entry_id}")
async def repair_knowledge_source_detail(entry_id: str, http_request: Request) -> dict:
    _get_agent_service(http_request)
    runtime_deps = await build_request_runtime_deps(http_request)
    if not is_frontend_source_display_enabled(runtime_deps):
        return {"success": False, "message": "来源展示未启用"}

    knowledge_service = getattr(runtime_deps, "repair_knowledge_service", None)
    if knowledge_service is None:
        return {"success": False, "message": "维修知识库未启用"}

    detail = knowledge_service.get_source_detail(entry_id)
    if detail is None:
        return {"success": False, "message": "未找到对应的维修经验"}

    return {"success": True, "data": detail}


@router.get("/parameter-query/source/{source_id}")
@router.get("/chat/api/parameter-query/source/{source_id}")
async def parameter_query_source_detail(source_id: str, http_request: Request) -> dict:
    _get_agent_service(http_request)
    runtime_deps = await build_request_runtime_deps(http_request)
    if not is_frontend_source_display_enabled(runtime_deps):
        return {"success": False, "message": "来源展示未启用"}

    parameter_service = getattr(runtime_deps, "parameter_query_service", None)
    if parameter_service is None:
        return {"success": False, "message": "参数查询未启用"}

    detail = parameter_service.get_source_detail(source_id)
    if detail is None:
        return {"success": False, "message": "未找到对应的参数资料"}

    return {"success": True, "data": detail}
