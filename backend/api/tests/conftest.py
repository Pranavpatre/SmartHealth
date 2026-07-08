"""Shared pytest fixtures."""
import os
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
import sys

# Set test env vars before importing anything else
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://smarthealth:smarthealth@localhost:5432/smarthealth_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-minimum!!")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("WHATSAPP_TOKEN", "test_token")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test_verify")
os.environ.setdefault("GEMINI_API_KEY", "test_key")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def async_client():
    """HTTP client that talks to the FastAPI app."""
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def mock_db_session():
    """Mock AsyncSession for unit tests that don't need a real DB."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
def sample_facility_id():
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def sample_user_token():
    """Generate a valid JWT for test use."""
    from auth.jwt import create_access_token
    return create_access_token(
        "00000000-0000-0000-0000-000000000099",
        extra={"role": "DISTRICT_OFFICER", "facility_id": None}
    )


# ── Shared endpoint-test fixture (used by test_endpoints_*.py) ────────────────
SUPER_ID = "11111111-1111-1111-1111-111111111111"
DO_ID = "22222222-2222-2222-2222-222222222222"
FW_ID = "33333333-3333-3333-3333-333333333333"
PHC_ID = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
async def ctx():
    """Insert test users (super/district-officer/field-worker/phc-admin) into
    the seeded DB and return (client, tokens, ids).

    The module-global engine uses a QueuePool bound to the loop it was first
    used on; pytest runs each async test on its own loop, which triggers
    "attached to a different loop" errors. Rebuild the engine (get_db() looks it
    up by name) with NullPool on the current loop for the duration of the test.
    """
    import db
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from auth.jwt import create_access_token
    from main import app

    orig_engine, orig_maker = db.engine, db.AsyncSessionLocal
    await orig_engine.dispose()
    engine = create_async_engine(db.settings.database_url, poolclass=NullPool, echo=False)
    db.engine = engine
    db.AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        fac = (await conn.execute(text("SELECT id FROM facilities LIMIT 1"))).scalar()
        did = (await conn.execute(text("SELECT id FROM districts LIMIT 1"))).scalar()
        for uid, role, dist, facility in [
            (SUPER_ID, "SUPERADMIN", None, None),
            (DO_ID, "DISTRICT_OFFICER", did, None),
            (FW_ID, "FIELD_WORKER", did, fac),
            (PHC_ID, "PHC_ADMIN", did, fac),
        ]:
            await conn.execute(
                text(
                    """
                    INSERT INTO users (id, role, name, phone, language_pref, is_active, district_id, facility_id)
                    VALUES (:id, CAST(:role AS user_role), :name, :phone, 'en', TRUE, :dist, :fac)
                    ON CONFLICT (id) DO UPDATE SET is_active = TRUE,
                        district_id = EXCLUDED.district_id, facility_id = EXCLUDED.facility_id
                    """
                ),
                {"id": uid, "role": role, "name": f"Test {role}",
                 "phone": f"+9199{uid[:8]}", "dist": dist, "fac": str(fac) if facility else None},
            )

    def tok(uid, role, facility=None):
        return create_access_token(uid, extra={"role": role, "facility_id": facility})

    tokens = {
        "super": tok(SUPER_ID, "SUPERADMIN"),
        "do": tok(DO_ID, "DISTRICT_OFFICER"),
        "fw": tok(FW_ID, "FIELD_WORKER", str(fac)),
        "phc": tok(PHC_ID, "PHC_ADMIN", str(fac)),
    }
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with client:
        yield client, tokens, {"facility": str(fac), "district": did}
    await engine.dispose()
    db.engine, db.AsyncSessionLocal = orig_engine, orig_maker


@pytest.fixture
def auth_header():
    """Return a helper: auth_header(token) -> Authorization header dict."""
    return lambda t: {"Authorization": f"Bearer {t}"}
