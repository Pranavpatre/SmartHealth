"""JWT token creation and validation tests."""
import pytest
from datetime import datetime, timezone


def test_create_access_token_contains_expected_claims():
    from auth.jwt import create_access_token, decode_token
    token = create_access_token("user-123", extra={"role": "FIELD_WORKER"})
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"
    assert payload["role"] == "FIELD_WORKER"
    assert "exp" in payload


def test_create_refresh_token_type():
    from auth.jwt import create_refresh_token, decode_token
    token = create_refresh_token("user-456")
    payload = decode_token(token)
    assert payload["type"] == "refresh"
    assert payload["sub"] == "user-456"


def test_decode_invalid_token_raises():
    from auth.jwt import decode_token
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        decode_token("not.a.valid.token")
    assert exc_info.value.status_code == 401


def test_access_token_different_from_refresh():
    from auth.jwt import create_access_token, create_refresh_token
    access = create_access_token("u1")
    refresh = create_refresh_token("u1")
    assert access != refresh
