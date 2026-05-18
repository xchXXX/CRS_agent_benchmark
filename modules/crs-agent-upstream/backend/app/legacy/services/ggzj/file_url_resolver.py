"""Resolve GGZJ file access URLs."""

import logging
import time
from typing import Optional
from urllib.parse import quote

import httpx

from app.legacy.services.ggzj.search_client import TokenExpiredError
from app.legacy.utils.token_utils import parse_jwt_source


logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(15.0, connect=10.0)
PDF_LOADER_BASE = "https://mft-static.51gonggui.com/pdf-loader/index.html"


class GgzjFileUrlResolver:
    """Resolve file URLs for external GGZJ documents."""

    WPS_URL = "https://wx.51gonggui.com/commonrail/api/soso-api/dataGeneralConfig/getWpsFileUrl.json"
    CIRCUIT_URL = "https://wx.51gonggui.com/commonrail/api/soso-api/circuitSchematicDiagram/query"
    WPS_PAGE_BASE = "https://wx.51gonggui.com/v4/pages/wps/wps"

    async def resolve(
        self,
        sn: int,
        data_type: int,
        file_no: Optional[str],
        file_type: Optional[str],
        app_token: str,
    ) -> dict:
        if not app_token:
            raise TokenExpiredError("未提供 token")

        if data_type == 2:
            return await self._resolve_wps(sn, file_type, app_token)
        if data_type == 3:
            return await self._resolve_circuit(sn, app_token)
        return self._resolve_legacy(file_no, file_type)

    async def _resolve_wps(self, sn: int, file_type: Optional[str], app_token: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    self.WPS_URL,
                    json={"id": str(sn)},
                    headers={
                        "Content-Type": "application/json",
                        "app-token": app_token,
                        "source": parse_jwt_source(app_token),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("[FileUrlResolver] WPS request failed: %s", exc)
            return {"url": None, "url_type": "error", "message": f"网络异常: {exc}"}

        if data.get("status") != 200:
            msg = data.get("msg", "获取文件链接失败")
            if "token" in msg.lower() or "登录" in msg or "权限" in msg:
                raise TokenExpiredError(msg)
            return {"url": None, "url_type": "error", "message": msg}

        token_data = data.get("data", {}).get("token", {})
        pic_folder_url = token_data.get("picFolderUrl")
        wps_url = token_data.get("wpsUrl")

        if file_type == "共轨之家图文" and pic_folder_url:
            return {"url": pic_folder_url, "url_type": "pic_folder"}
        if pic_folder_url and "/wps/file/zip/" in pic_folder_url:
            return {"url": self._build_pdf_loader_url(pic_folder_url), "url_type": "pdf_loader"}
        if wps_url:
            return {"url": wps_url, "url_type": "wps_page"}
        return {"url": f"{self.WPS_PAGE_BASE}?fileNo={sn}", "url_type": "wps_page"}

    @staticmethod
    def _build_pdf_loader_url(pic_folder_url: str) -> str:
        ts = int(time.time() * 1000)
        encoded = quote(pic_folder_url, safe=":/?#[]@!$&'()*+,;=-._~")
        return f"{PDF_LOADER_BASE}?{ts}#/?file={encoded}"

    async def _resolve_circuit(self, sn: int, app_token: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    self.CIRCUIT_URL,
                    json={"dataId": f"-{sn}"},
                    headers={
                        "Content-Type": "application/json",
                        "app-token": app_token,
                        "source": parse_jwt_source(app_token),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("[FileUrlResolver] circuit request failed: %s", exc)
            return {"url": None, "url_type": "error", "message": f"网络异常: {exc}"}

        if data.get("status") != 200:
            msg = data.get("msg", "获取电路图链接失败")
            if "token" in msg.lower() or "登录" in msg or "权限" in msg:
                raise TokenExpiredError(msg)
            return {"url": None, "url_type": "error", "message": msg}

        circuit_data = data.get("data", {})
        url = circuit_data.get("url") or circuit_data.get("picFolderUrl")
        if not url:
            return {"url": None, "url_type": "error", "message": "未获取到电路图链接"}
        return {"url": url, "url_type": "circuit_page"}

    @staticmethod
    def _resolve_legacy(file_no: Optional[str], file_type: Optional[str]) -> dict:
        return {
            "url": None,
            "url_type": "legacy",
            "file_no": file_no,
            "file_type": file_type,
            "message": "暂不支持此类型文件的在线预览",
        }
