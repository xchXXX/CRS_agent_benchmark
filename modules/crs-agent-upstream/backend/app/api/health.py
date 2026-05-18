"""Health endpoints."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/chat/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "crs-agent-backend"}
