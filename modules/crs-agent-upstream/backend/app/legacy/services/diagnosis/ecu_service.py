"""ECU 服务."""

import logging
import time

import httpx

from app.core.config import settings
from app.legacy.services.config_service import config_service

logger = logging.getLogger(__name__)


class ECUService:
    """从外部诊断服务获取 ECU 列表并做短期缓存。"""

    def __init__(self):
        self._cache: list[str] = []
        self._cache_time: float = 0

    @property
    def _cache_ttl(self) -> int:
        return int(config_service.get("diagnosis_ecu_cache_ttl", settings.diagnosis_ecu_cache_ttl))

    @property
    def _service_url(self) -> str:
        url = str(config_service.get("diagnosis_service_url", settings.diagnosis_service_url))
        if url and not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        return url

    @property
    def _timeout(self) -> int:
        return int(config_service.get("diagnosis_timeout", settings.diagnosis_timeout))

    @property
    def _ecu_list_url(self) -> str:
        path = str(config_service.get("diagnosis_ecu_list_path", settings.diagnosis_ecu_list_path))
        return f"{self._service_url}{path}"

    async def get_ecu_list(self, force_refresh: bool = False) -> list[str]:
        if not force_refresh and self._is_cache_valid():
            return self._cache

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._ecu_list_url)
                response.raise_for_status()
                data = response.json()

            if isinstance(data, list):
                ecu_list = data
            elif isinstance(data, dict):
                ecu_list = data.get("ecuModels", data.get("ecus", data.get("data", [])))
            else:
                logger.error("ECU列表响应格式异常: %s", type(data))
                ecu_list = []

            self._cache = ecu_list
            self._cache_time = time.time()
            return ecu_list
        except httpx.RequestError as exc:
            logger.error("获取ECU列表网络错误: %s", exc)
            return self._cache if self._cache else []
        except Exception as exc:
            logger.exception("获取ECU列表异常: %s", exc)
            return self._cache if self._cache else []

    async def is_valid_ecu(self, ecu_model: str) -> bool:
        ecu_list = await self.get_ecu_list()
        normalized = ecu_model.upper().strip()
        return any(ecu.upper().strip() == normalized for ecu in ecu_list)

    async def find_matching_ecus(self, query: str, limit: int = 5) -> list[str]:
        ecu_list = await self.get_ecu_list()
        query_upper = query.upper().strip()

        exact_matches = [ecu for ecu in ecu_list if ecu.upper() == query_upper]
        if exact_matches:
            return exact_matches[:limit]

        prefix_matches = [ecu for ecu in ecu_list if ecu.upper().startswith(query_upper)]
        if prefix_matches:
            return prefix_matches[:limit]

        contains_matches = [ecu for ecu in ecu_list if query_upper in ecu.upper()]
        return contains_matches[:limit]

    def _is_cache_valid(self) -> bool:
        return bool(self._cache) and (time.time() - self._cache_time) < self._cache_ttl


_ecu_service: ECUService | None = None


def get_ecu_service() -> ECUService:
    global _ecu_service
    if _ecu_service is None:
        _ecu_service = ECUService()
    return _ecu_service
