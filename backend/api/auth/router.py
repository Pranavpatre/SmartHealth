import secrets
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt import create_access_token, create_refresh_token, decode_token, get_current_user
from config import get_settings
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
    facility_id: str | None = None
    facility_name: str | None = None
    district_id: int | None = None
    district_name: str | None = None
    state_id: int | None = None
    state_name: str | None = None
    language_pref: str


@router.post("/otp/request", status_code=status.HTTP_200_OK)
async def request_otp(body: OTPRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.phone == body.phone, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        # Return 200 to avoid phone enumeration
        return {"message": "If this number is registered, an OTP has been sent."}

    settings = get_settings()
    otp = f"{secrets.randbelow(1_000_000):06d}"  # 6-digit numeric OTP (matches the UI input)
    _otp_store[body.phone] = (otp, datetime.now(timezone.utc) + timedelta(minutes=10))

    # TODO: Send via WhatsApp/SMS integration. Until that is wired, surface the
    # OTP in the server logs for non-production so local login works. In
    # non-production the configured dev OTP is also always accepted on verify.
    if settings.is_production:
        log.info("otp_generated", phone=body.phone, otp="***")
    else:
        log.info("otp_generated", phone=body.phone, otp=otp, dev_otp=settings.dev_login_otp)
    return {"message": "If this number is registered, an OTP has been sent."}


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_otp(body: OTPVerify, db: AsyncSession = Depends(get_db)):
    settings = get_settings()

    # Dev-only bypass: in non-production, accept the configured dev OTP for any
    # existing active user (local SMS/WhatsApp delivery is not wired up).
    dev_bypass = (not settings.is_production) and body.otp == settings.dev_login_otp

    if not dev_bypass:
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
        select(User).where(User.phone == body.phone, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access = create_access_token(user.id, extra={"role": user.role, "facility_id": str(user.facility_id) if user.facility_id else None})
    refresh = create_refresh_token(user.id)

    facility_name = None
    if user.facility_id:
        from models.facility import Facility
        fac_result = await db.execute(select(Facility.name).where(Facility.id == user.facility_id))
        facility_name = fac_result.scalar_one_or_none()

    # Resolve the user's district + state so the dashboard can auto-scope its
    # filters (e.g. a district officer lands with their state/district selected).
    district_name = state_id = state_name = None
    if user.district_id is not None:
        from sqlalchemy import text as _sa_text
        geo = (await db.execute(
            _sa_text("SELECT d.name, s.id, s.name FROM districts d JOIN states s ON s.id = d.state_id WHERE d.id = :did"),
            {"did": user.district_id},
        )).first()
        if geo:
            district_name, state_id, state_name = geo[0], geo[1], geo[2]

    log.info("user_login", user_id=str(user.id), role=user.role)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user_id=str(user.id),
        role=user.role,
        name=user.name,
        facility_id=str(user.facility_id) if user.facility_id else None,
        facility_name=facility_name,
        district_id=user.district_id,
        district_name=district_name,
        state_id=state_id,
        state_name=state_name,
        language_pref=user.language_pref,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    from models.user import User

    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    result = await db.execute(
        select(User).where(User.id == payload["sub"], User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access = create_access_token(user.id, extra={"role": user.role, "facility_id": str(user.facility_id) if user.facility_id else None})
    refresh = create_refresh_token(user.id)

    facility_name = None
    if user.facility_id:
        from models.facility import Facility
        fac_result = await db.execute(select(Facility.name).where(Facility.id == user.facility_id))
        facility_name = fac_result.scalar_one_or_none()

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user_id=str(user.id),
        role=user.role,
        name=user.name,
        facility_id=str(user.facility_id) if user.facility_id else None,
        facility_name=facility_name,
        language_pref=user.language_pref,
    )


# ---------------------------------------------------------------------------
# Set the caller's working location from GPS coordinates.
# Lets the dashboard follow the user's ACTUAL location (nearest facility's
# district) instead of a static assigned district. Persisted on the user row so
# every scoped view (dashboard/facilities/planning) re-scopes accordingly.
# ---------------------------------------------------------------------------

class SetLocationRequest(BaseModel):
    lat: float
    lng: float


class SetLocationResponse(BaseModel):
    district_id: int | None
    district_name: str | None
    state_id: int | None
    state_name: str | None


@router.post("/me/location", response_model=SetLocationResponse)
async def set_my_location(
    body: SetLocationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text as _sa_text

    # Nearest facility to the caller's GPS → its district (+ state) via PostGIS KNN.
    geo = (await db.execute(
        _sa_text(
            """
            SELECT d.id AS district_id, d.name AS district_name,
                   s.id AS state_id, s.name AS state_name
            FROM facilities f
            JOIN districts d ON d.id = f.district_id
            JOIN states s ON s.id = d.state_id
            WHERE f.location IS NOT NULL
            ORDER BY f.location <-> ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)
            LIMIT 1
            """
        ),
        {"lat": body.lat, "lng": body.lng},
    )).mappings().first()

    if not geo:
        raise HTTPException(status_code=404, detail="No facility near this location.")

    # Persist so all server-scoped views (dashboard/facilities/planning) follow it.
    current_user.district_id = geo["district_id"]
    await db.commit()

    return SetLocationResponse(
        district_id=geo["district_id"], district_name=geo["district_name"],
        state_id=geo["state_id"], state_name=geo["state_name"],
    )
