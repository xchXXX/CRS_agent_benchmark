"""Qwen VL based image evidence extraction."""

from __future__ import annotations

import json
import logging
import re
from uuid import uuid4

from pydantic import ValidationError

from app.agent.domain.image_evidence.models import (
    ImageEvidenceAnalysis,
    ImageEvidenceDiagnosticInfo,
    ImageEvidenceImageInput,
    ImageEvidenceRequest,
    ImageEvidenceResponse,
    ImageEvidenceScene,
    ImageEvidenceVehicleInfo,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class ImageEvidenceService:
    """Extract structured vehicle and diagnostic evidence from user images."""

    _FAULT_CODE_RE = re.compile(r"\b[PCBU][0-9A-F]{4,6}\b", re.IGNORECASE)
    _SCENE_HINTS = (
        ("diagnostic", ImageEvidenceScene.DIAGNOSTIC_SCREEN),
        ("诊断", ImageEvidenceScene.DIAGNOSTIC_SCREEN),
        ("报码", ImageEvidenceScene.DIAGNOSTIC_SCREEN),
        ("故障码", ImageEvidenceScene.DIAGNOSTIC_SCREEN),
        ("仪表", ImageEvidenceScene.DIAGNOSTIC_SCREEN),
        ("document", ImageEvidenceScene.DOCUMENT_HINT),
        ("资料", ImageEvidenceScene.DOCUMENT_HINT),
        ("铭牌", ImageEvidenceScene.DOCUMENT_HINT),
        ("ecu", ImageEvidenceScene.DOCUMENT_HINT),
        ("控制器", ImageEvidenceScene.DOCUMENT_HINT),
        ("电脑板", ImageEvidenceScene.DOCUMENT_HINT),
        ("vehicle", ImageEvidenceScene.VEHICLE_IDENTITY),
        ("车型", ImageEvidenceScene.VEHICLE_IDENTITY),
        ("整车", ImageEvidenceScene.VEHICLE_IDENTITY),
        ("repair", ImageEvidenceScene.REPAIR_SCENE),
        ("维修", ImageEvidenceScene.REPAIR_SCENE),
        ("现场", ImageEvidenceScene.REPAIR_SCENE),
    )

    def __init__(self, config_service=None):
        self._config_service = config_service

    async def analyze(self, request: ImageEvidenceRequest) -> ImageEvidenceResponse:
        images = request.images[: self._max_images]
        if not images:
            return ImageEvidenceResponse(
                success=False,
                error={"code": "NO_IMAGE", "message": "请至少上传一张图片。"},
            )

        for image in images:
            if len(image.content) > self._max_image_bytes:
                return ImageEvidenceResponse(
                    success=False,
                    error={"code": "IMAGE_TOO_LARGE", "message": f"单张图片大小不能超过 {self._max_image_mb}MB。"},
                )

        if not self._enabled:
            return ImageEvidenceResponse(
                success=False,
                error={"code": "IMAGE_EVIDENCE_DISABLED", "message": "图片证据识别未启用。"},
            )

        try:
            evidence = await self._analyze_with_model(images=images, user_prompt=request.user_prompt)
            return ImageEvidenceResponse(success=True, evidence=evidence)
        except Exception as exc:
            logger.exception("image evidence analysis failed: %s", exc)
            return ImageEvidenceResponse(
                success=False,
                error={"code": "IMAGE_EVIDENCE_MODEL_ERROR", "message": str(exc)},
            )

    async def _analyze_with_model(
        self,
        *,
        images: list[ImageEvidenceImageInput],
        user_prompt: str | None,
    ) -> ImageEvidenceAnalysis:
        from pydantic_ai import Agent, BinaryContent

        api_key = self._api_key
        if not api_key:
            raise RuntimeError("缺少图片识别模型 API Key，请配置 CRS_IMAGE_EVIDENCE_API_KEY 或 OPENROUTER_API_KEY。")
        model = self._build_model(api_key=api_key)
        agent = Agent(
            model=model,
            output_type=str,
            instructions=self._system_prompt,
            retries=1,
            defer_model_check=True,
        )
        prompt_parts: list[object] = [self._build_user_prompt(user_prompt=user_prompt, image_count=len(images))]
        for image in images:
            prompt_parts.append(BinaryContent(data=image.content, media_type=image.content_type))

        result = await agent.run(prompt_parts)
        return self._parse_text_output(str(result.output or ""))

    def _build_model(self, *, api_key: str):
        base_url = self._base_url
        model_name = self._model_name
        if "openrouter.ai" in base_url:
            from pydantic_ai.models.openrouter import OpenRouterModel
            from pydantic_ai.providers.openrouter import OpenRouterProvider

            return OpenRouterModel(model_name, provider=OpenRouterProvider(api_key=api_key, app_title="crs-agent"))

        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIChatModel(model_name, provider=OpenAIProvider(base_url=base_url, api_key=api_key))

    def _parse_text_output(self, output: str) -> ImageEvidenceAnalysis:
        text = (output or "").strip()
        data: dict = {}
        if text:
            try:
                data = self._coerce_model_payload(json.loads(self._extract_json_object(text)))
            except Exception:
                data = {"summary": text, "visible_text": [text]}
        try:
            return self._normalize_evidence(ImageEvidenceAnalysis.model_validate(data))
        except ValidationError:
            return self._normalize_evidence(
                ImageEvidenceAnalysis(
                    image_evidence_id=f"img_{uuid4().hex}",
                    scene=ImageEvidenceScene.UNKNOWN,
                    summary=text[:400],
                    visible_text=[text[:1000]] if text else [],
                    confidence=0.3 if text else 0.0,
                    raw={"raw_output": text},
                )
            )

    def _coerce_model_payload(self, data: object) -> dict:
        if not isinstance(data, dict):
            return {"summary": str(data or ""), "visible_text": [str(data or "")] if data else []}

        payload = dict(data)

        scene = self._normalize_scene(payload.get("scene"))
        if scene is not None:
            payload["scene"] = scene

        vehicle = payload.get("vehicle")
        if isinstance(vehicle, str):
            payload["vehicle"] = {
                "brand": vehicle.strip() or None,
            }
        elif vehicle is None:
            payload["vehicle"] = {}
        elif isinstance(vehicle, dict):
            vehicle_payload = dict(vehicle)
            emission = vehicle_payload.get("emission")
            if not emission and vehicle_payload.get("emission_standard"):
                vehicle_payload["emission"] = vehicle_payload.get("emission_standard")
            payload["vehicle"] = vehicle_payload

        diagnosis = payload.get("diagnosis")
        if diagnosis is None:
            diagnosis_payload: dict[str, object] = {}
        elif isinstance(diagnosis, dict):
            diagnosis_payload = dict(diagnosis)
        else:
            diagnosis_payload = {"descriptions": [str(diagnosis).strip()]} if str(diagnosis).strip() else {}

        fault_code = diagnosis_payload.pop("fault_code", None)
        if fault_code and not diagnosis_payload.get("fault_codes"):
            diagnosis_payload["fault_codes"] = [fault_code]

        description = diagnosis_payload.pop("description", None)
        if description and not diagnosis_payload.get("descriptions"):
            diagnosis_payload["descriptions"] = [description]

        system = diagnosis_payload.get("system")
        if system and not diagnosis_payload.get("ecu_model"):
            diagnosis_payload["ecu_model"] = system

        payload["diagnosis"] = diagnosis_payload

        raw = payload.get("raw")
        if isinstance(raw, str):
            payload["raw"] = {"raw_output": raw}
        elif raw is None:
            payload["raw"] = {}

        visible_text = payload.get("visible_text")
        if isinstance(visible_text, str):
            payload["visible_text"] = [visible_text]

        suggested_queries = payload.get("suggested_queries")
        if isinstance(suggested_queries, str):
            payload["suggested_queries"] = [suggested_queries]

        if not payload.get("image_evidence_id"):
            payload["image_evidence_id"] = f"img_{uuid4().hex}"

        return payload

    def _normalize_evidence(self, evidence: ImageEvidenceAnalysis) -> ImageEvidenceAnalysis:
        payload = evidence.model_dump(mode="json")
        if not payload.get("image_evidence_id"):
            payload["image_evidence_id"] = f"img_{uuid4().hex}"

        diagnosis = payload.get("diagnosis") or {}
        fault_codes = []
        for code in diagnosis.get("fault_codes") or []:
            normalized = self._normalize_fault_code(code)
            if normalized and normalized not in fault_codes:
                fault_codes.append(normalized)
        for text in payload.get("visible_text") or []:
            for code in self._FAULT_CODE_RE.findall(str(text or "")):
                normalized = self._normalize_fault_code(code)
                if normalized and normalized not in fault_codes:
                    fault_codes.append(normalized)
        diagnosis["fault_codes"] = fault_codes
        payload["diagnosis"] = diagnosis

        payload["summary"] = str(payload.get("summary") or "").strip()[:500]
        payload["visible_text"] = [str(item).strip()[:500] for item in payload.get("visible_text") or [] if str(item).strip()][:8]
        payload["suggested_queries"] = [
            str(item).strip()[:120] for item in payload.get("suggested_queries") or [] if str(item).strip()
        ][:5]
        if not payload["summary"]:
            payload["summary"] = self._build_fallback_summary(payload)
        payload["needs_user_confirm"] = bool(payload.get("needs_user_confirm", True))
        return ImageEvidenceAnalysis.model_validate(payload)

    @classmethod
    def _normalize_scene(cls, scene: object) -> ImageEvidenceScene | None:
        raw = str(scene or "").strip()
        if not raw:
            return None

        try:
            return ImageEvidenceScene(raw)
        except Exception:
            normalized = raw.lower()
            for hint, mapped in cls._SCENE_HINTS:
                if hint in normalized:
                    return mapped
        return ImageEvidenceScene.UNKNOWN

    @classmethod
    def _normalize_fault_code(cls, code: object) -> str | None:
        normalized = str(code or "").strip().upper().replace(" ", "")
        if not normalized:
            return None
        match = cls._FAULT_CODE_RE.search(normalized)
        return match.group(0).upper() if match else normalized

    @staticmethod
    def _extract_json_object(text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no json object")
        return text[start : end + 1]

    @staticmethod
    def _build_fallback_summary(payload: dict) -> str:
        vehicle = payload.get("vehicle") or {}
        diagnosis = payload.get("diagnosis") or {}
        parts = [
            vehicle.get("brand"),
            vehicle.get("series"),
            vehicle.get("model"),
            vehicle.get("engine"),
            vehicle.get("emission"),
        ]
        vehicle_text = " ".join(str(item) for item in parts if item)
        codes = ", ".join(diagnosis.get("fault_codes") or [])
        if vehicle_text and codes:
            return f"图片识别到车辆信息：{vehicle_text}；故障码：{codes}。"
        if vehicle_text:
            return f"图片识别到车辆信息：{vehicle_text}。"
        if codes:
            return f"图片识别到故障码：{codes}。"
        visible_text = payload.get("visible_text") or []
        if visible_text:
            return f"图片文字识别：{visible_text[0]}"
        return "图片已识别，但未提取到明确车辆或故障信息。"

    @staticmethod
    def _build_user_prompt(*, user_prompt: str | None, image_count: int) -> str:
        prompt = (
            f"用户上传了 {image_count} 张汽车维修相关图片。"
            "请识别图片中的车辆身份、诊断仪信息、故障码、故障描述、铭牌文字、资料检索线索。"
            "如果图片是车型外观，请重点判断品牌、车系、车型、平台、发动机、排放阶段。"
            "如果图片是诊断仪或仪表，请重点提取故障码、报码描述、ECU/系统、当前或历史状态。"
            "如果图片无法确定，请不要编造，保留 visible_text 和 summary。"
        )
        if user_prompt:
            prompt += f"\n用户补充说明：{user_prompt.strip()}"
        return prompt

    @property
    def _enabled(self) -> bool:
        return bool(self._get_config("image_evidence_enabled", settings.image_evidence_enabled))

    @property
    def _model_name(self) -> str:
        return str(self._get_config("image_evidence_model", settings.image_evidence_model)).strip()

    @property
    def _api_key(self) -> str | None:
        value = self._get_config("image_evidence_api_key", settings.image_evidence_api_key)
        if not value:
            import os

            value = os.getenv("OPENROUTER_API_KEY")
        return str(value or "").strip() or None

    @property
    def _base_url(self) -> str:
        value = self._get_config("image_evidence_base_url", settings.image_evidence_base_url)
        if not value:
            import os

            value = os.getenv("OPENROUTER_BASE_URL") or settings.image_evidence_base_url
        return str(value or "").strip().rstrip("/")

    @property
    def _max_images(self) -> int:
        return max(1, int(self._get_config("image_evidence_max_images", settings.image_evidence_max_images)))

    @property
    def _max_image_mb(self) -> int:
        return max(1, int(self._get_config("image_evidence_max_image_mb", settings.image_evidence_max_image_mb)))

    @property
    def _max_image_bytes(self) -> int:
        return self._max_image_mb * 1024 * 1024

    @property
    def _system_prompt(self) -> str:
        return (
            "你是商用车、柴油共轨、电控维修场景的图片证据识别器。"
            "你只输出单个 JSON 对象，不输出 markdown、代码块或解释性正文。"
            "JSON 顶层字段必须兼容 ImageEvidenceAnalysis："
            "image_evidence_id, scene, summary, vehicle, diagnosis, visible_text, suggested_queries, confidence, needs_user_confirm, raw。"
            "字段不确定时填 null 或空数组，不要猜。"
            "品牌、车系、车型、发动机、排放阶段、故障码必须来自图片可见内容或高度确定的视觉线索。"
            "summary 用中文概括图片中对后续资料搜索、故障诊断、维修问答有帮助的信息。"
            "suggested_queries 生成 1 到 5 个可用于资料搜索或维修问答的中文查询，不要包含无依据信息。"
            "confidence 表示整体识别可信度，图片模糊或遮挡时降低。"
            "raw 可保留模型提取依据摘要，但不要放长篇原文。"
        )

    def _get_config(self, key: str, default):
        if self._config_service is None:
            return default
        return self._config_service.get(key, default)
