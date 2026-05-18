"""配置热更新服务 - 单例模式"""

import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.agent.model_ids import normalize_configured_model
from app.core.config import settings
from app.legacy.models.admin_models import SystemConfig
from app.legacy.models.database import get_session_local

logger = logging.getLogger(__name__)


MODEL_CONFIG_KEYS = {"agent_model", "openrouter_clarify_model", "intent_router_model"}


class ConfigService:
    """配置服务 - 支持热更新"""

    _instance = None
    _config_cache: Dict[str, Any] = {}
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._load_from_db()
            ConfigService._initialized = True

    def _load_from_db(self):
        """从数据库加载所有配置到内存"""
        try:
            SessionLocal = get_session_local()
            db = SessionLocal()
            try:
                configs = db.query(SystemConfig).all()
                for config in configs:
                    self._config_cache[config.config_key] = self._parse_value(
                        config.config_value, config.config_type
                    )
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Legacy config bootstrap skipped; using static settings. reason=%s", exc)

    def _parse_value(self, value: str, config_type: str) -> Any:
        """根据类型解析配置值"""
        if config_type == 'int':
            return int(value)
        elif config_type == 'float':
            return float(value)
        elif config_type == 'bool':
            return value.lower() in ('true', '1', 'yes')
        elif config_type == 'json':
            return json.loads(value)
        return value

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值（优先内存缓存 → settings默认值）"""
        if key in self._config_cache:
            value = self._config_cache[key]
        else:
            value = getattr(settings, key, default)

        if key in MODEL_CONFIG_KEYS:
            return normalize_configured_model(value)
        return value

    def set(self, key: str, value: Any, config_type: str, updated_by: str, db: Session,
            category: str = None, description: str = None, is_sensitive: bool = False) -> bool:
        """
        设置配置值（写DB + 更新内存）

        如果配置不存在且提供了 category，则创建新配置
        """
        try:
            # 统一做类型转换，避免前端/接口层以字符串传入导致缓存类型错误
            parsed_value: Any
            if config_type == 'int':
                parsed_value = int(value)
                str_value = str(parsed_value)
            elif config_type == 'float':
                parsed_value = float(value)
                str_value = str(parsed_value)
            elif config_type == 'bool':
                if isinstance(value, bool):
                    parsed_value = value
                else:
                    parsed_value = str(value).lower() in ('true', '1', 'yes')
                str_value = 'true' if parsed_value else 'false'
            elif config_type == 'json':
                if isinstance(value, str):
                    parsed_value = json.loads(value)
                else:
                    parsed_value = value
                str_value = json.dumps(parsed_value, ensure_ascii=False)
            else:
                parsed_value = str(value)
                str_value = parsed_value

            if key in MODEL_CONFIG_KEYS:
                parsed_value = normalize_configured_model(parsed_value)
                str_value = str(parsed_value)

            config = db.query(SystemConfig).filter(
                SystemConfig.config_key == key
            ).first()

            if config:
                # 更新已存在的配置
                config.config_value = str_value
                config.config_type = config_type
                config.updated_by = updated_by
                logger.debug(f"更新配置: {key} = {value if not config.is_sensitive else '******'}")
            elif category:
                # 创建新配置
                config = SystemConfig(
                    config_key=key,
                    config_value=str_value,
                    config_type=config_type,
                    category=category,
                    description=description or "",
                    is_sensitive=is_sensitive,
                    updated_by=updated_by
                )
                db.add(config)
                logger.info(f"创建新配置: {key} = {value if not is_sensitive else '******'}")
            else:
                logger.warning(f"配置 {key} 不存在且未提供 category，无法创建")
                return False

            db.commit()
            # 缓存写入已解析的类型值，避免下游直接拿到字符串（例如 httpx timeout）
            self._config_cache[key] = parsed_value
            return True
        except Exception as e:
            logger.error(f"设置配置 {key} 失败: {e}")
            db.rollback()
            return False

    def refresh(self):
        """刷新内存缓存"""
        self._config_cache.clear()
        self._load_from_db()

    def get_by_category(self, category: str, db: Session) -> list:
        """按分类获取配置列表"""
        configs = db.query(SystemConfig).filter(
            SystemConfig.category == category
        ).all()
        return [
            {
                "id": c.id,
                "key": c.config_key,
                "value": c.config_value if not c.is_sensitive else "******",
                "type": c.config_type,
                "description": c.description,
                "is_sensitive": c.is_sensitive,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                "updated_by": c.updated_by
            }
            for c in configs
        ]

    def get_all_categories(self, db: Session) -> Dict[str, list]:
        """获取所有分类的配置"""
        configs = db.query(SystemConfig).order_by(SystemConfig.id).all()
        result = {}
        for c in configs:
            if c.category not in result:
                result[c.category] = []
            result[c.category].append({
                "id": c.id,
                "key": c.config_key,
                "value": c.config_value if not c.is_sensitive else "******",
                "type": c.config_type,
                "description": c.description,
                "is_sensitive": c.is_sensitive
            })
        return result


# 全局单例
config_service = ConfigService()
