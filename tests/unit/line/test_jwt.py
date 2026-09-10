from collections.abc import Callable
from typing import Any

import pytest

from matoca_service.line.jwt import (
    TokenFormatError,
    TokenRelationshipError,
    decode_jwt_unverified,
    validate_native_pair,
)

type JwtFactory = Callable[[dict[str, Any]], str]


def access_claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "jti": "access-1",
        "rtid": "family-1",
        "aud": "LINE",
        "scp": "LINE_CORE",
        "iat": 1_789_000_000,
        "exp": 1_789_604_800,
    }
    claims.update(overrides)
    return claims


def refresh_claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "jti": "family-1",
        "ati": "access-1",
        "aud": "LINE",
        "rot": "ROTATE",
        "aid": "u-synthetic",
        "lsid": "session-synthetic",
        "iat": 1_789_000_000,
        "exp": 1_820_536_000,
    }
    claims.update(overrides)
    return claims


def test_decode_jwt_unverified_returns_parts(jwt_factory: JwtFactory) -> None:
    token = jwt_factory(access_claims())

    parts = decode_jwt_unverified(token)

    assert parts.header == {"alg": "RS256", "typ": "JWT"}
    assert parts.payload["jti"] == "access-1"
    assert parts.signature == "synthetic-signature"


def test_decode_jwt_rejects_malformed_token_without_echoing_it() -> None:
    token = "not-a-secret-token"

    with pytest.raises(TokenFormatError) as error:
        decode_jwt_unverified(token)

    assert token not in str(error.value)


def test_native_pair_links_refresh_family_and_access_token(jwt_factory: JwtFactory) -> None:
    access = jwt_factory(access_claims())
    refresh = jwt_factory(refresh_claims())

    claims = validate_native_pair(access, refresh)

    assert claims.rtid == "family-1"
    assert claims.access_jti == "access-1"
    assert claims.aid == "u-synthetic"
    assert claims.lsid == "session-synthetic"


def test_native_pair_rejects_mismatched_ati(jwt_factory: JwtFactory) -> None:
    access = jwt_factory(access_claims())
    refresh = jwt_factory(refresh_claims(ati="other-access"))

    with pytest.raises(TokenRelationshipError, match="access token identifier"):
        validate_native_pair(access, refresh)


def test_native_pair_rejects_refresh_expiring_before_access(jwt_factory: JwtFactory) -> None:
    access = jwt_factory(access_claims(exp=1_800_000_000))
    refresh = jwt_factory(refresh_claims(exp=1_799_999_999))

    with pytest.raises(TokenRelationshipError, match="expiry"):
        validate_native_pair(access, refresh)
