import secrets
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt import create_access_token, create_refresh_token, decode_token
from db import get_db
from models.user import User

log = structlog.get_logger()
router = APIRouter(prefix="/auth")

# In production: use Redis TTL store for OTPs
_otp_store: dict[str, tuple[str, datetime]] = {}


class OTPRequest(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if not v.startswith("+"):
            v = "+91" + v.lstrip("0")
        return v


class OTPVerify(BaseModel):
    phone: str
    otp: str

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if not v.startswith("+"):
            v = "+91" + v.lstrip("0")
        return v


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str
    role: str
    name: str


@router.post("/auth/otp/request", status_code=status.HTTP_200_OK)
async def request_otp(body: OTPRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.phone == body.phone, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        # Return 200 to avoid phone enumeration
        return {"message": "If this number is registered, an OTP has been sent."}

    otp = secrets.token_hex(3).upper()  # 6-char hex OTP
    _otp_store[body.phone] = (otp, datetime.now(timezone.utc) + timedelta(minutes=10))

    # TODO: Send via WhatsApp/SMS integration
    log.info("otp_generated", phone=body.phone, otp=otp if not user else "***")
    return {"message": "If this number is registered, an OTP has been sent."}


@router.post("/auth/otp/verify", response_model=TokenResponse)
async def verify_otp(body: OTPVerify, db: AsyncSession = Depends(get_db)):
    stored = _otp_store.get(body.phone)
    if not stored:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OTP not found or expired")

    stored_otp, expires_at = stored
    if datetime.now(timezone.utc) > expires_at:
        del _otp_store[body.phone]
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OTP expired")

    if body.otp.upper() != stored_otp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OTP")

    del _otp_store[body.phone]

    result = await db.execute(
        select(User).where(User.phone == body.phone, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access = create_access_token(user.id, extra={"role": user.role, "facility_id": str(user.facility_id) if user.facility_id else None})
    refresh = create_refresh_token(user.id)

    log.info("user_login", user_id=str(user.id), role=user.role)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user_id=str(user.id),
        role=user.role,
        name=user.name,
    )


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    from models.user import User

    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    result = await db.execute(
        select(User).where(User.id == payload["sub"], User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access = create_access_token(user.id, extra={"role": user.role, "facility_id": str(user.facility_id) if user.facility_id else None})
    refresh = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user_id=str(user.id),
        role=user.role,
        name=user.name,
    )
