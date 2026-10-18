# backend/core/security.py
# 最小 JWT（HS256，标准库实现）与本地三角色账号

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from backend.config import get_settings
from backend.core.exceptions import AuthenticationError

# 仅供本地联调。生产环境应换成外部用户目录。
_DEMO_USERS: dict[str, dict[str, str]] = {
    "submitter": {
        "password": "submitter123",
        "role": "submitter",
        "user_id": "user_submitter",
    },
    "reviewer": {
        "password": "reviewer123",
        "role": "reviewer",
        "user_id": "user_reviewer",
    },
    "admin": {
        "password": "admin123",
        "role": "admin",
        "user_id": "user_admin",
    },
}


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _sign(payload: dict, secret: str) -> str:
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode("utf-8"), f"{header}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64encode(sig)}"


def create_access_token(user: dict) -> tuple[str, int]:
    settings = get_settings()
    expires_in = int(settings.jwt_expire_minutes) * 60
    now = int(time.time())
    payload = {
        "sub": user["user_id"],
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + expires_in,
    }
    return _sign(payload, settings.jwt_secret), expires_in


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        header, body, sig = token.split(".")
    except ValueError as exc:
        raise AuthenticationError("令牌格式无效") from exc
    expected = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        f"{header}.{body}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        given = _b64decode(sig)
    except Exception as exc:
        raise AuthenticationError("令牌签名无效") from exc
    if not hmac.compare_digest(given, expected):
        raise AuthenticationError("令牌签名无效")
    try:
        payload = json.loads(_b64decode(body))
    except Exception as exc:
        raise AuthenticationError("令牌载荷无效") from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise AuthenticationError("令牌已过期")
    return payload


def authenticate(username: str, password: str) -> dict | None:
    row = _DEMO_USERS.get((username or "").strip())
    if not row or not hmac.compare_digest(password or "", row["password"]):
        return None
    return {"username": username.strip(), "user_id": row["user_id"], "role": row["role"]}


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "user_id": payload.get("sub") or "",
        "username": payload.get("username") or "",
        "role": payload.get("role") or "",
    }


async def get_optional_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict | None:
    if not authorization:
        return None
    return await get_current_user(authorization)


def require_roles(*roles: str):
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return _dep


# 任意已登录角色（提交人 / 评审专家 / 管理员）
require_authenticated = get_current_user


def can_view_plan(user: dict, row: dict) -> bool:
    """评审专家可看全部；提交人仅看本人上传。"""
    role = user.get("role") or ""
    if role in ("reviewer", "admin"):
        return True
    return (row.get("submitter_id") or "") == (user.get("user_id") or "")
