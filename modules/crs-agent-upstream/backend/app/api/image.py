"""Diagnosis image and batch compatibility endpoints."""

import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.agent.adapters.legacy_fault_diag_adapter import LegacyFaultDiagAdapter
from app.agent.domain.image_evidence import ImageEvidenceImageInput, ImageEvidenceRequest, ImageEvidenceService
from app.api.request_context import build_request_runtime_deps
from app.core.config import settings

router = APIRouter(tags=["diagnosis"])
logger = logging.getLogger(__name__)


class BatchEcusRequest(BaseModel):
    fault_codes: list[str]


class BatchReportsRequest(BaseModel):
    fault_codes: list[str]
    ecu_model: str
    return_url: str | None = None


class OssUploadPolicyRequest(BaseModel):
    filename: str | None = None
    content_type: str | None = None
    session_id: str | None = None


class OssDeleteObjectRequest(BaseModel):
    key: str
    delete_token: str


class OssDeleteObjectsRequest(BaseModel):
    session_id: str | None = None
    reason: str = "new_search"
    objects: list[OssDeleteObjectRequest]


async def _read_image_upload(image: UploadFile, *, max_mb: int | None = None) -> ImageEvidenceImageInput:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片文件")

    content = await image.read()
    limit_mb = max_mb or settings.image_evidence_max_image_mb
    if len(content) > limit_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"图片大小不能超过{limit_mb}MB")

    return ImageEvidenceImageInput(
        filename=image.filename or "image.jpg",
        content=content,
        content_type=image.content_type,
    )


def _get_config(runtime_deps, key: str, default):
    if runtime_deps.config_service is None:
        return default
    return runtime_deps.config_service.get(key, default)


def _get_non_empty_config(runtime_deps, key: str, default):
    value = _get_config(runtime_deps, key, default)
    if isinstance(value, str) and not value.strip():
        return default
    return value if value is not None else default


def _normalize_oss_endpoint(endpoint: str) -> str:
    normalized = str(endpoint or "").strip()
    if not normalized:
        return ""
    normalized = normalized.removeprefix("https://").removeprefix("http://").strip("/")
    return normalized


def _safe_oss_extension(filename: str | None, content_type: str | None) -> str:
    raw = str(filename or "").rsplit(".", 1)
    ext = raw[-1].lower() if len(raw) > 1 else ""
    if ext and len(ext) <= 8 and ext.replace("-", "").isalnum():
        return ext
    normalized_content_type = str(content_type or "").lower()
    if "png" in normalized_content_type:
        return "png"
    if "webp" in normalized_content_type:
        return "webp"
    return "jpg"


def _build_policy_signature(policy_base64: str, access_key_secret: str) -> str:
    digest = hmac.new(
        access_key_secret.encode("utf-8"),
        policy_base64.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


@router.post("/image/oss-upload-policy")
@router.post("/chat/api/image/oss-upload-policy")
async def create_image_oss_upload_policy(payload: OssUploadPolicyRequest, raw_request: Request) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)
    enabled = bool(_get_config(runtime_deps, "aliyun_oss_image_upload_enabled", settings.aliyun_oss_image_upload_enabled))
    if not enabled:
        raise HTTPException(status_code=400, detail="图片 OSS 上传未启用")

    access_key_id = str(
        _get_non_empty_config(runtime_deps, "aliyun_oss_access_key_id", settings.aliyun_oss_access_key_id)
        or _get_non_empty_config(runtime_deps, "aliyun_speech_access_key_id", settings.aliyun_speech_access_key_id)
        or ""
    ).strip()
    access_key_secret = str(
        _get_non_empty_config(runtime_deps, "aliyun_oss_access_key_secret", settings.aliyun_oss_access_key_secret)
        or _get_non_empty_config(runtime_deps, "aliyun_speech_access_key_secret", settings.aliyun_speech_access_key_secret)
        or ""
    ).strip()
    if not access_key_id or not access_key_secret:
        raise HTTPException(status_code=400, detail="图片 OSS 上传配置不完整")

    bucket = str(_get_non_empty_config(runtime_deps, "aliyun_oss_bucket_name", settings.aliyun_oss_bucket_name) or "").strip()
    endpoint = _normalize_oss_endpoint(
        _get_non_empty_config(runtime_deps, "aliyun_oss_endpoint", settings.aliyun_oss_endpoint)
    )
    region = str(_get_non_empty_config(runtime_deps, "aliyun_oss_region", settings.aliyun_oss_region) or "").strip()
    image_dir = str(_get_non_empty_config(runtime_deps, "aliyun_oss_image_dir", settings.aliyun_oss_image_dir) or "chat_images").strip("/")
    expire_seconds = max(
        60,
        min(3600, int(_get_config(runtime_deps, "aliyun_oss_policy_expire_seconds", settings.aliyun_oss_policy_expire_seconds))),
    )
    max_image_mb = max(1, int(_get_config(runtime_deps, "aliyun_oss_max_image_mb", settings.aliyun_oss_max_image_mb)))
    if not bucket or not endpoint:
        raise HTTPException(status_code=400, detail="图片 OSS bucket 或 endpoint 未配置")

    session_part = str(payload.session_id or "anonymous").strip().replace("/", "_")[:64] or "anonymous"
    ext = _safe_oss_extension(payload.filename, payload.content_type)
    object_key = f"{image_dir}/{session_part}/{datetime.now(UTC).strftime('%Y%m%d')}/{uuid.uuid4().hex}.{ext}"
    expire_at = datetime.now(UTC) + timedelta(seconds=expire_seconds)
    policy = {
        "expiration": expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "conditions": [
            ["content-length-range", 1, max_image_mb * 1024 * 1024],
            ["eq", "$key", object_key],
        ],
    }
    policy_base64 = base64.b64encode(json.dumps(policy, separators=(",", ":")).encode("utf-8")).decode("utf-8")
    signature = _build_policy_signature(policy_base64, access_key_secret)
    host = f"https://{bucket}.{endpoint}"
    delete_token = None
    if bool(_get_config(runtime_deps, "aliyun_oss_delete_enabled", settings.aliyun_oss_delete_enabled)):
        try:
            from app.legacy.services.oss_image_delete_service import OssImageDeleteService

            delete_token = OssImageDeleteService(config_service=runtime_deps.config_service).create_delete_token(
                object_key=object_key,
                session_id=payload.session_id,
                user_id=runtime_deps.user_id,
            )
        except Exception:
            logger.exception("create OSS delete token failed")
            delete_token = None
    return {
        "success": True,
        "access_id": access_key_id,
        "policy": policy_base64,
        "signature": signature,
        "dir": image_dir,
        "key": object_key,
        "host": host,
        "url": f"{host}/{object_key}",
        "bucket": bucket,
        "endpoint": endpoint,
        "region": region,
        "expire_at": expire_at.isoformat(),
        "max_image_mb": max_image_mb,
        "delete_token": delete_token,
    }


@router.post("/image/oss-delete-objects", status_code=202)
@router.post("/chat/api/image/oss-delete-objects", status_code=202)
async def delete_image_oss_objects(payload: OssDeleteObjectsRequest, raw_request: Request) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)
    if not payload.objects:
        return {"success": True, "accepted": 0, "skipped": 0, "invalid": 0}
    if len(payload.objects) > 50:
        raise HTTPException(status_code=400, detail="一次最多提交 50 张图片删除")

    session_factory = getattr(runtime_deps, "db_session_factory", None)
    if session_factory is None:
        raise HTTPException(status_code=503, detail="图片删除任务服务不可用")

    from app.legacy.services.oss_image_delete_service import OssImageDeleteService

    service = OssImageDeleteService(session_factory=session_factory, config_service=runtime_deps.config_service)
    try:
        result = service.enqueue_delete_jobs(
            objects=[item.model_dump() for item in payload.objects],
            session_id=payload.session_id,
            user_id=runtime_deps.user_id,
            reason=payload.reason,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"success": True, **result}


def _legacy_fault_code_response_from_evidence(evidence) -> dict:
    diagnosis = evidence.diagnosis
    fault_codes = [
        {
            "raw": code,
            "normalized": code,
            "type": "DTC",
            "description": diagnosis.descriptions[idx] if idx < len(diagnosis.descriptions) else "",
            "status": diagnosis.status,
            "selected": True,
        }
        for idx, code in enumerate(diagnosis.fault_codes)
    ]
    return {
        "success": True,
        "fault_codes": fault_codes,
        "count": len(fault_codes),
        "image_evidence": evidence.model_dump(mode="json"),
    }


@router.get("/image/diagnosis-available")
@router.get("/chat/api/image/diagnosis-available")
async def get_diagnosis_available(raw_request: Request) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)
    enabled = settings.diagnosis_service_enabled
    if runtime_deps.config_service is not None:
        enabled = bool(runtime_deps.config_service.get("diagnosis_service_enabled", enabled))
    return {"available": bool(enabled)}


@router.get("/image/evidence-available")
@router.get("/chat/api/image/evidence-available")
async def get_image_evidence_available(raw_request: Request) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)
    enabled = settings.image_evidence_enabled
    if runtime_deps.config_service is not None:
        enabled = bool(runtime_deps.config_service.get("image_evidence_enabled", enabled))
    return {
        "available": bool(enabled),
        "max_images": int(settings.image_evidence_max_images),
        "max_image_mb": int(settings.image_evidence_max_image_mb),
    }


@router.post("/image/analyze-evidence")
@router.post("/chat/api/image/analyze-evidence")
async def analyze_image_evidence(
    raw_request: Request,
    images: list[UploadFile] = File(...),
) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)
    if not images:
        raise HTTPException(status_code=400, detail="请至少上传一张图片")
    max_images = int(settings.image_evidence_max_images)
    if len(images) > max_images:
        raise HTTPException(status_code=400, detail=f"一次最多上传{max_images}张图片")

    image_inputs = [await _read_image_upload(image) for image in images]
    result = await ImageEvidenceService(config_service=runtime_deps.config_service).analyze(
        ImageEvidenceRequest(images=image_inputs)
    )
    return result.model_dump(mode="json")


@router.post("/image/recognize-fault-codes")
@router.post("/chat/api/image/recognize-fault-codes")
async def recognize_fault_codes(raw_request: Request, image: UploadFile = File(...)) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)
    image_input = await _read_image_upload(image, max_mb=15)

    evidence_result = await ImageEvidenceService(config_service=runtime_deps.config_service).analyze(
        ImageEvidenceRequest(images=[image_input])
    )
    if evidence_result.success and evidence_result.evidence is not None:
        response = _legacy_fault_code_response_from_evidence(evidence_result.evidence)
        if response["fault_codes"]:
            return response

    adapter = LegacyFaultDiagAdapter(runtime_deps)
    legacy_response = await adapter.recognize_image(image_input.content, image_input.filename)
    if evidence_result.evidence is not None and isinstance(legacy_response, dict):
        legacy_response["image_evidence"] = evidence_result.evidence.model_dump(mode="json")
    return legacy_response


@router.post("/diagnosis/batch-ecus")
@router.post("/chat/api/diagnosis/batch-ecus")
async def get_batch_ecus(request: BatchEcusRequest, raw_request: Request) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)
    if not request.fault_codes:
        raise HTTPException(status_code=400, detail="故障码列表不能为空")
    if len(request.fault_codes) > 20:
        raise HTTPException(status_code=400, detail="一次最多查询20个故障码")

    adapter = LegacyFaultDiagAdapter(runtime_deps)
    return await adapter.get_batch_ecus(request.fault_codes)


@router.post("/diagnosis/batch-reports")
@router.post("/chat/api/diagnosis/batch-reports")
async def get_batch_reports(request: BatchReportsRequest, raw_request: Request) -> dict:
    runtime_deps = await build_request_runtime_deps(raw_request)
    if not request.fault_codes:
        raise HTTPException(status_code=400, detail="故障码列表不能为空")
    if not request.ecu_model:
        raise HTTPException(status_code=400, detail="ECU型号不能为空")
    if len(request.fault_codes) > 20:
        raise HTTPException(status_code=400, detail="一次最多查询20个故障码")

    adapter = LegacyFaultDiagAdapter(runtime_deps)
    return await adapter.get_batch_reports(request.fault_codes, request.ecu_model, request.return_url)
