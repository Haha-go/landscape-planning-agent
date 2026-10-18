# backend/api/v1/auth.py
# POST /api/v1/auth/login — 本地三角色 JWT

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.security import authenticate, create_access_token

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(..., description="submitter / reviewer / admin")
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    user_id: str
    username: str


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    user = authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, expires_in = create_access_token(user)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        role=user["role"],
        user_id=user["user_id"],
        username=user["username"],
    )
