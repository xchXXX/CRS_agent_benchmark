"""GGZJ file URL endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.request_context import extract_app_token
from app.legacy.services.ggzj.search_client import TokenExpiredError


logger = logging.getLogger(__name__)
router = APIRouter(tags=["ggzj"])


class FileUrlRequest(BaseModel):
    sn: int
    data_type: int
    file_no: Optional[str] = None
    file_type: Optional[str] = None


@router.post("/ggzj/file-url")
@router.post("/chat/api/ggzj/file-url")
async def get_file_url(request: FileUrlRequest, raw_request: Request):
    app_token = extract_app_token(raw_request.headers, raw_request.query_params)
    if not app_token:
        raise HTTPException(status_code=401, detail="未登录，请重新进入")

    deps = getattr(raw_request.app.state, "runtime_deps", None)
    resolver = getattr(deps, "ggzj_file_url_resolver", None) if deps is not None else None
    if resolver is None:
        from app.legacy.services.ggzj.file_url_resolver import GgzjFileUrlResolver

        resolver = GgzjFileUrlResolver()

    try:
        return await resolver.resolve(
            sn=request.sn,
            data_type=request.data_type,
            file_no=request.file_no,
            file_type=request.file_type,
            app_token=app_token,
        )
    except TokenExpiredError as exc:
        logger.warning("[ggzj/file-url] token expired: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[ggzj/file-url] unexpected error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
