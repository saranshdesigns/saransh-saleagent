"""
Dashboard Authentication Helper
Simple password-based JWT auth. Single-user (Owner only).
"""
import os
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, Header

SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "saransh-dashboard-secret-change-this")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 12

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain: str) -> bool:
    """Check plain password against DASHBOARD_PASSWORD in .env"""
    stored = os.getenv("DASHBOARD_PASSWORD", "")
    if not stored:
        return False
    # Support both plain-text and bcrypt-hashed passwords in .env
    if stored.startswith("$2b$"):
        return pwd_context.verify(plain, stored)
    return plain == stored


def create_access_token(data: dict) -> str:
    payload = data.copy()
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload.update({"exp": expire})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def require_auth(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency — validates Bearer token on every protected route."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    return decode_token(token)
