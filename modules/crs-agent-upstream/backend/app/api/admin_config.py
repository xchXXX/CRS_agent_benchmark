"""系统配置管理接口"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.legacy.models.admin_models import SystemConfig
from app.legacy.models.database import get_db
from app.legacy.services.config_service import config_service
from app.legacy.utils.auth import TokenData, get_current_user, require_admin


router = APIRouter(prefix="/admin/config", tags=["admin-config"])
logger = logging.getLogger(__name__)

LOCKED_CONFIG_KEYS = {"user_auth_enabled"}


class ConfigUpdateItem(BaseModel):
    """单个配置更新项"""

    key: str
    value: str
    type: str


class ConfigUpdateRequest(BaseModel):
    """批量更新配置请求"""

    configs: list[ConfigUpdateItem]


@router.get("/list")
async def get_all_configs(
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取所有配置（按分类）"""
    del current_user
    return config_service.get_all_categories(db)


@router.get("/category/{category}")
async def get_configs_by_category(
    category: str,
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取指定分类的配置"""
    del current_user
    return config_service.get_by_category(category, db)


@router.put("/update")
async def update_configs(
    data: ConfigUpdateRequest,
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量更新配置"""
    updated: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    locked: list[str] = []

    for item in data.configs:
        if item.key in LOCKED_CONFIG_KEYS:
            locked.append(item.key)
            continue

        existing_config = db.query(SystemConfig).filter(SystemConfig.config_key == item.key).first()

        if existing_config and existing_config.is_sensitive and item.value == "******":
            skipped.append(item.key)
            continue

        success = config_service.set(
            key=item.key,
            value=item.value,
            config_type=item.type,
            updated_by=current_user.username,
            db=db,
        )
        if success:
            updated.append(item.key)
        else:
            failed.append(item.key)

    message_parts = [f"成功更新 {len(updated)} 项配置"]
    if skipped:
        message_parts.append(f"跳过 {len(skipped)} 项敏感配置")
    if locked:
        message_parts.append(f"忽略 {len(locked)} 项固定配置")

    return {
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
        "locked": locked,
        "message": "，".join(message_parts),
    }


@router.post("/refresh")
async def refresh_config_cache(
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """刷新配置缓存（并尝试补齐缺失的默认配置项）"""
    del current_user
    reconcile_result = {"created_count": 0, "deleted_count": 0, "updated_meta_count": 0}
    try:
        from app.legacy.services.config_initializer import reconcile_system_configs

        reconcile_result = reconcile_system_configs(db)
    except Exception:
        pass

    config_service.refresh()
    return {
        "message": "配置缓存已刷新",
        "created_count": reconcile_result["created_count"],
        "deleted_count": reconcile_result["deleted_count"],
        "updated_meta_count": reconcile_result["updated_meta_count"],
    }


@router.delete("/log-file")
async def clear_log_file(
    current_user: TokenData = Depends(require_admin),
):
    """清空日志文件内容（截断为空）"""
    log_path = Path(__file__).resolve().parent.parent.parent / "logs" / "app.log"
    if not log_path.exists():
        return {"message": "日志文件不存在，无需清除"}

    try:
        with open(log_path, "w", encoding="utf-8") as file_obj:
            file_obj.truncate(0)
        logger.info("日志文件已被 %s 清空", current_user.username)
        return {"message": "日志文件已清空"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"清空日志文件失败: {exc}") from exc
