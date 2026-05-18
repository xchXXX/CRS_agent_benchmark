"""诊断服务客户端."""

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.legacy.services.config_service import config_service

logger = logging.getLogger(__name__)


@dataclass
class DiagnosisResult:
    success: bool
    state: str
    fault_code: str
    ecu_model: str
    report_url: str | None
    task_id: str | None
    subscribe_url: str | None
    report_id: int | None
    error: dict | None = None


@dataclass
class EcusByFaultCodeResult:
    success: bool
    fault_code: str
    ecu_models: list[str]
    count: int
    message: str
    error: dict | None = None


@dataclass
class RecognizedFaultCode:
    raw: str
    normalized: str
    code_type: str
    description: str
    status: str | None


@dataclass
class ImageRecognitionResult:
    success: bool
    fault_codes: list[RecognizedFaultCode]
    count: int
    error: dict | None = None


@dataclass
class EcuSummaryItem:
    ecu_model: str
    match_count: int
    matched_codes: list[str]
    recommended: bool


@dataclass
class BatchEcusResult:
    success: bool
    ecu_summary: list[EcuSummaryItem]
    code_details: dict[str, list[str]]
    error: dict | None = None


@dataclass
class BatchReportItem:
    fault_code: str
    state: str
    report_url: str | None
    task_id: str | None
    subscribe_url: str | None
    report_id: int | None


@dataclass
class BatchReportsResult:
    success: bool
    ecu_model: str
    reports: list[BatchReportItem]
    error: dict | None = None


class DiagnosisServiceClient:
    MAX_RETRIES = 3
    RETRY_DELAY = 0.5

    @property
    def _base_url(self) -> str:
        url = str(config_service.get("diagnosis_service_url", settings.diagnosis_service_url))
        if url and not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        return url

    @property
    def _timeout(self) -> int:
        return int(config_service.get("diagnosis_timeout", settings.diagnosis_timeout))

    @property
    def _image_timeout(self) -> int:
        return int(config_service.get("diagnosis_image_timeout", settings.diagnosis_image_timeout))

    @property
    def _ensure_latest_url(self) -> str:
        path = str(config_service.get("diagnosis_ensure_latest_path", settings.diagnosis_ensure_latest_path))
        return f"{self._base_url}{path}"

    @property
    def _ensure_latest_no_back_url(self) -> str:
        path = str(
            config_service.get(
                "diagnosis_ensure_latest_no_back_path",
                settings.diagnosis_ensure_latest_no_back_path,
            )
        )
        return f"{self._base_url}{path}"

    @property
    def _ecus_by_fault_code_url(self) -> str:
        path = str(
            config_service.get(
                "diagnosis_ecus_by_fault_code_path",
                settings.diagnosis_ecus_by_fault_code_path,
            )
        )
        return f"{self._base_url}{path}"

    async def get_ecus_by_fault_code(self, fault_code: str) -> EcusByFaultCodeResult:
        import asyncio

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(
                        self._ecus_by_fault_code_url,
                        params={"faultCode": fault_code.strip().upper()},
                    )

                data = response.json()
                if response.status_code == 200 and data.get("success"):
                    return EcusByFaultCodeResult(
                        success=True,
                        fault_code=data.get("faultCode", fault_code),
                        ecu_models=data.get("ecuModels", []),
                        count=data.get("count", 0),
                        message=data.get("message", ""),
                    )

                return EcusByFaultCodeResult(
                    success=False,
                    fault_code=fault_code,
                    ecu_models=[],
                    count=0,
                    message="",
                    error=data.get("error", {"message": "请求失败"}),
                )
            except httpx.TimeoutException:
                last_error = {"code": "TIMEOUT", "message": "请求超时"}
                logger.warning("故障码反查ECU请求超时 (%s/%s): %s", attempt + 1, self.MAX_RETRIES, fault_code)
            except httpx.RequestError as exc:
                last_error = {"code": "NETWORK_ERROR", "message": str(exc)}
                logger.warning("故障码反查ECU网络错误 (%s/%s): %s", attempt + 1, self.MAX_RETRIES, exc)
            except Exception as exc:
                last_error = {"code": "INTERNAL_ERROR", "message": str(exc)}
                logger.warning("故障码反查ECU异常 (%s/%s): %s", attempt + 1, self.MAX_RETRIES, exc)

            if attempt < self.MAX_RETRIES - 1:
                await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))

        return EcusByFaultCodeResult(
            success=False,
            fault_code=fault_code,
            ecu_models=[],
            count=0,
            message="",
            error=last_error or {"code": "UNKNOWN", "message": "请求失败"},
        )

    async def ensure_latest(
        self,
        ecu_model: str,
        fault_code: str,
        no_back: bool = False,
        show_back: bool = True,
        return_url: str | None = None,
    ) -> DiagnosisResult:
        import asyncio

        last_error = None
        url = self._ensure_latest_no_back_url if no_back else self._ensure_latest_url
        request_body = {
            "ecu": ecu_model.strip(),
            "faultCode": fault_code.strip().upper(),
            "showBack": show_back,
        }
        if return_url:
            request_body["returnUrl"] = return_url

        for attempt in range(self.MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=request_body)

                data = response.json()
                if response.status_code == 200 and data.get("success"):
                    return DiagnosisResult(
                        success=True,
                        state=data.get("state", "unknown"),
                        fault_code=data.get("dtcCode", fault_code),
                        ecu_model=data.get("ecuModel", ecu_model),
                        report_url=data.get("reportViewerUrl"),
                        task_id=data.get("taskId"),
                        subscribe_url=data.get("subscribeUrl"),
                        report_id=data.get("reportId"),
                    )

                return DiagnosisResult(
                    success=False,
                    state="failed",
                    fault_code=fault_code,
                    ecu_model=ecu_model,
                    report_url=None,
                    task_id=None,
                    subscribe_url=None,
                    report_id=None,
                    error=data.get("error", {}),
                )
            except httpx.TimeoutException:
                last_error = {"code": "TIMEOUT", "message": "诊断服务请求超时"}
                logger.warning("诊断服务请求超时 (%s/%s): %s %s", attempt + 1, self.MAX_RETRIES, ecu_model, fault_code)
            except httpx.RequestError as exc:
                last_error = {"code": "NETWORK_ERROR", "message": str(exc)}
                logger.warning("诊断服务网络错误 (%s/%s): %s", attempt + 1, self.MAX_RETRIES, exc)
            except Exception as exc:
                last_error = {"code": "INTERNAL_ERROR", "message": str(exc)}
                logger.warning("诊断服务调用异常 (%s/%s): %s", attempt + 1, self.MAX_RETRIES, exc)

            if attempt < self.MAX_RETRIES - 1:
                await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))

        return DiagnosisResult(
            success=False,
            state="failed",
            fault_code=fault_code,
            ecu_model=ecu_model,
            report_url=None,
            task_id=None,
            subscribe_url=None,
            report_id=None,
            error=last_error or {"code": "UNKNOWN", "message": "请求失败"},
        )

    async def recognize_image(self, image_content: bytes, filename: str) -> ImageRecognitionResult:
        path = str(
            config_service.get(
                "diagnosis_image_recognize_path",
                settings.diagnosis_image_recognize_path,
            )
        )
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._image_timeout) as client:
                files = {"image": (filename, image_content)}
                response = await client.post(url, files=files)

            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    fault_codes = [
                        RecognizedFaultCode(
                            raw=item.get("raw", ""),
                            normalized=item.get("normalized", ""),
                            code_type=item.get("type", "OTHER"),
                            description=item.get("description", ""),
                            status=item.get("status"),
                        )
                        for item in data
                    ]
                    return ImageRecognitionResult(success=True, fault_codes=fault_codes, count=len(fault_codes))

                return ImageRecognitionResult(
                    success=False,
                    fault_codes=[],
                    count=0,
                    error={"message": data.get("error", "识别失败")},
                )

            return ImageRecognitionResult(
                success=False,
                fault_codes=[],
                count=0,
                error={"code": f"HTTP_{response.status_code}", "message": response.text},
            )
        except httpx.TimeoutException:
            return ImageRecognitionResult(
                success=False,
                fault_codes=[],
                count=0,
                error={"code": "TIMEOUT", "message": "图片识别请求超时"},
            )
        except httpx.RequestError as exc:
            return ImageRecognitionResult(
                success=False,
                fault_codes=[],
                count=0,
                error={"code": "NETWORK_ERROR", "message": str(exc)},
            )
        except Exception as exc:
            logger.exception("图片识别异常: %s", exc)
            return ImageRecognitionResult(
                success=False,
                fault_codes=[],
                count=0,
                error={"code": "INTERNAL_ERROR", "message": str(exc)},
            )

    async def get_batch_ecus(self, fault_codes: list[str]) -> BatchEcusResult:
        try:
            code_details: dict[str, list[str]] = {}
            ecu_count: dict[str, list[str]] = {}

            for code in fault_codes:
                result = await self.get_ecus_by_fault_code(code)
                if result.success:
                    code_details[code] = result.ecu_models
                    for ecu in result.ecu_models:
                        ecu_count.setdefault(ecu, []).append(code)
                else:
                    code_details[code] = []

            ecu_summary = [
                EcuSummaryItem(
                    ecu_model=ecu,
                    match_count=len(matched_codes),
                    matched_codes=matched_codes,
                    recommended=False,
                )
                for ecu, matched_codes in sorted(ecu_count.items(), key=lambda item: len(item[1]), reverse=True)
            ]
            if ecu_summary:
                ecu_summary[0].recommended = True

            return BatchEcusResult(success=True, ecu_summary=ecu_summary, code_details=code_details)
        except Exception as exc:
            logger.exception("批量ECU查询异常: %s", exc)
            return BatchEcusResult(
                success=False,
                ecu_summary=[],
                code_details={},
                error={"code": "INTERNAL_ERROR", "message": str(exc)},
            )

    async def get_batch_reports(
        self,
        fault_codes: list[str],
        ecu_model: str,
        return_url: str | None = None,
    ) -> BatchReportsResult:
        try:
            reports: list[BatchReportItem] = []
            for code in fault_codes:
                result = await self.ensure_latest(ecu_model, code, return_url=return_url)
                if result.success:
                    reports.append(
                        BatchReportItem(
                            fault_code=code,
                            state=result.state,
                            report_url=result.report_url,
                            task_id=result.task_id,
                            subscribe_url=result.subscribe_url,
                            report_id=result.report_id,
                        )
                    )
                else:
                    reports.append(
                        BatchReportItem(
                            fault_code=code,
                            state="not_found",
                            report_url=None,
                            task_id=None,
                            subscribe_url=None,
                            report_id=None,
                        )
                    )

            return BatchReportsResult(success=True, ecu_model=ecu_model, reports=reports)
        except Exception as exc:
            logger.exception("批量报告查询异常: %s", exc)
            return BatchReportsResult(
                success=False,
                ecu_model=ecu_model,
                reports=[],
                error={"code": "INTERNAL_ERROR", "message": str(exc)},
            )


_diagnosis_client: DiagnosisServiceClient | None = None


def get_diagnosis_client() -> DiagnosisServiceClient:
    global _diagnosis_client
    if _diagnosis_client is None:
        _diagnosis_client = DiagnosisServiceClient()
    return _diagnosis_client
