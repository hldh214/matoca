import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from matoca_service.line.models import NativePairClaims


class TokenFormatError(ValueError):
    """Raised when a token is not a structurally valid JWT."""


class TokenRelationshipError(ValueError):
    """Raised when native access and refresh token claims do not agree."""


@dataclass(frozen=True, slots=True)
class JwtParts:
    header: dict[str, Any]
    payload: dict[str, Any]
    signature: str


def _decode_segment(segment: str) -> dict[str, Any]:
    padding = "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(segment + padding)
        value = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TokenFormatError("JWT contains an invalid JSON segment") from error
    if not isinstance(value, dict):
        raise TokenFormatError("JWT header and payload must be JSON objects")
    return value


def decode_jwt_unverified(token: str) -> JwtParts:
    """Decode a JWT without verifying its signature or trusting its claims."""
    segments = token.split(".")
    if len(segments) != 3 or not all(segments):
        raise TokenFormatError("JWT must contain three non-empty segments")
    return JwtParts(
        header=_decode_segment(segments[0]),
        payload=_decode_segment(segments[1]),
        signature=segments[2],
    )


def _required_str(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise TokenRelationshipError(f"required string claim is missing: {name}")
    return value


def _optional_str(claims: dict[str, Any], name: str) -> str | None:
    value = claims.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise TokenRelationshipError(f"optional claim has invalid type: {name}")
    return value


def _required_timestamp(claims: dict[str, Any], name: str) -> datetime:
    value = claims.get(name)
    if not isinstance(value, int):
        raise TokenRelationshipError(f"required timestamp claim is missing: {name}")
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        message = f"timestamp claim is outside supported range: {name}"
        raise TokenRelationshipError(message) from error


def validate_native_pair(access_token: str, refresh_token: str) -> NativePairClaims:
    """Validate captured LINE access/refresh claim relationships.

    This is structural validation only. It does not verify either JWT signature.
    """
    access = decode_jwt_unverified(access_token).payload
    refresh = decode_jwt_unverified(refresh_token).payload

    access_jti = _required_str(access, "jti")
    access_rtid = _required_str(access, "rtid")
    refresh_jti = _required_str(refresh, "jti")
    refresh_ati = _required_str(refresh, "ati")
    access_audience = _required_str(access, "aud")
    refresh_audience = _required_str(refresh, "aud")

    if refresh_jti != access_rtid:
        raise TokenRelationshipError("refresh credential family does not match access token")
    if refresh_ati != access_jti:
        raise TokenRelationshipError("refresh access token identifier does not match access token")
    if refresh_audience != access_audience:
        raise TokenRelationshipError("token audiences do not match")

    access_issued_at = _required_timestamp(access, "iat")
    access_expires_at = _required_timestamp(access, "exp")
    refresh_issued_at = _required_timestamp(refresh, "iat")
    refresh_expires_at = _required_timestamp(refresh, "exp")
    if refresh_expires_at <= access_expires_at:
        raise TokenRelationshipError("refresh expiry must be later than access expiry")

    return NativePairClaims(
        access_jti=access_jti,
        rtid=access_rtid,
        aid=_optional_str(refresh, "aid"),
        lsid=_optional_str(refresh, "lsid"),
        audience=access_audience,
        scope=_optional_str(access, "scp"),
        access_issued_at=access_issued_at,
        access_expires_at=access_expires_at,
        refresh_issued_at=refresh_issued_at,
        refresh_expires_at=refresh_expires_at,
    )
