"""GGZJ external search client."""

import logging
from typing import List

import httpx

from app.legacy.utils.token_utils import parse_jwt_source


logger = logging.getLogger(__name__)


class TokenExpiredError(Exception):
    """Raised when an app token is invalid or expired."""


class GgzjSearchClient:
    """Client for external GGZJ means search."""

    SEARCH_URL = "https://wx.51gonggui.com/commonrail/api/management/getMeansList.json"
    VALIDATE_URL = "https://wx.51gonggui.com/commonrail/api/member-api/userLoginInfo.json"
    PARENT_CLASS_ID = "0"
    PAGE_SIZE = 20
    TARGET_COUNT = 150
    MAX_PAGES = 8
    TIMEOUT = 15.0

    async def search(self, query: str, app_token: str) -> List[dict]:
        if not app_token:
            raise TokenExpiredError("未提供 token")

        await self._validate_token(app_token)

        all_items: List[dict] = []
        for page in range(1, self.MAX_PAGES + 1):
            page_items = await self._fetch_page(query, app_token, page)
            all_items.extend(page_items)

            if len(page_items) < self.PAGE_SIZE or len(all_items) >= self.TARGET_COUNT:
                break

        logger.info("[GgzjSearch] finished with %s items", len(all_items))
        return all_items

    async def _validate_token(self, app_token: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                resp = await client.post(
                    self.VALIDATE_URL,
                    json={},
                    headers={
                        "app-token": app_token,
                        "source": parse_jwt_source(app_token),
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("[GgzjSearch] token validation failed: %s", exc)
            raise TokenExpiredError(f"token 校验网络异常: {exc}") from exc

        if (
            isinstance(data, dict)
            and data.get("status") == 200
            and isinstance(data.get("data"), dict)
            and data["data"].get("userId")
        ):
            return True

        msg = data.get("msg", "token 无效") if isinstance(data, dict) else "token 无效"
        raise TokenExpiredError(msg)

    async def _fetch_page(self, query: str, app_token: str, page: int) -> List[dict]:
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                resp = await client.post(
                    self.SEARCH_URL,
                    json={
                        "parentClassId": self.PARENT_CLASS_ID,
                        "dataName": query,
                        "pageNum": page,
                        "pageSize": self.PAGE_SIZE,
                    },
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "app-token": app_token,
                        "source": parse_jwt_source(app_token),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("[GgzjSearch] page fetch failed page=%s: %s", page, exc)
            return []

        if data.get("status") == 200:
            return data.get("data", {}).get("dataList", [])

        logger.warning("[GgzjSearch] upstream returned error: %s", data.get("msg", "unknown"))
        return []
