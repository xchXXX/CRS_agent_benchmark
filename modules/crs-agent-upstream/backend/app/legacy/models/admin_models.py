"""后台管理系统数据模型"""

from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, Boolean
from sqlalchemy.sql import func
from app.legacy.models.database import Base


class AdminUser(Base):
    """管理员用户表"""
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(
        Enum('super_admin', 'admin', 'viewer', name='admin_role'),
        default='admin'
    )
    is_active = Column(Boolean, default=True, index=True)
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class SystemConfig(Base):
    """系统配置表"""
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(100), unique=True, nullable=False, index=True)
    config_value = Column(Text, nullable=False)
    config_type = Column(
        Enum('string', 'int', 'float', 'bool', 'json', name='config_type'),
        default='string'
    )
    category = Column(String(50), nullable=False, index=True)
    description = Column(String(255), nullable=True)
    is_sensitive = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    updated_by = Column(String(50), nullable=True)
