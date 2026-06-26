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
