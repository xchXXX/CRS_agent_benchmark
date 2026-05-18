"""Admin authentication endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.legacy.models.admin_models import AdminUser
from app.legacy.models.database import get_db
from app.legacy.utils.auth import (
    TokenData,
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)


router = APIRouter(tags=["admin-auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400
    user: dict


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/admin/auth/login", response_model=LoginResponse)
@router.post("/chat/api/admin/auth/login", response_model=LoginResponse)
async def login(
    request: Request,
    login_data: LoginRequest,
    db: Session = Depends(get_db),
):
    user = db.query(AdminUser).filter(AdminUser.username == login_data.username).first()
    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")

    user.last_login_at = datetime.now()
    user.last_login_ip = request.client.host if request.client else None
    db.commit()

    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.id, "role": user.role}
    )
    return LoginResponse(
        access_token=access_token,
        user={"id": user.id, "username": user.username, "role": user.role},
    )


@router.get("/admin/auth/me")
@router.get("/chat/api/admin/auth/me")
async def get_current_user_info(
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(AdminUser).filter(AdminUser.id == current_user.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.put("/admin/auth/password")
@router.put("/chat/api/admin/auth/password")
async def change_password(
    data: PasswordChangeRequest,
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(AdminUser).filter(AdminUser.id == current_user.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")

    user.password_hash = get_password_hash(data.new_password)
    db.commit()
    return {"message": "密码修改成功"}
