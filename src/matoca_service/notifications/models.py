import base64
import binascii
from datetime import datetime
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, field_validator


def decode_key(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError, binascii.Error:
        raise ValueError("通知の鍵が不正です") from None


class SubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    p256dh: str = Field(repr=False, max_length=100)
    auth: str = Field(repr=False, max_length=30)

    @field_validator("p256dh")
    @classmethod
    def valid_point(cls, value: str) -> str:
        point = decode_key(value)
        if len(point) != 65 or point[0] != 4:
            raise ValueError("通知の公開鍵が不正です")
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        except ValueError:
            raise ValueError("通知の公開鍵が不正です") from None
        return value

    @field_validator("auth")
    @classmethod
    def valid_auth(cls, value: str) -> str:
        if len(decode_key(value)) != 16:
            raise ValueError("通知の鍵が不正です")
        return value


class PushSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    endpoint: str = Field(repr=False, max_length=4096)
    keys: SubscriptionKeys = Field(repr=False)
    expirationTime: float | None = Field(default=None, exclude=True)

    @field_validator("endpoint")
    @classmethod
    def vendor_endpoint(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            allowed = host == "fcm.googleapis.com" or host == "updates.push.services.mozilla.com"
            allowed = allowed or host == "web.push.apple.com" or host.endswith(".push.apple.com")
            if (
                parsed.scheme != "https"
                or not allowed
                or parsed.port not in (None, 443)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or not parsed.path
                or any(c.isspace() for c in value)
            ):
                raise ValueError
        except ValueError:
            raise ValueError("対応するブラウザーのHTTPS通知先を指定してください") from None
        return value


class Notification(BaseModel):
    id: int
    kind: str
    title: str
    body: str
    url: str
    created_at: datetime


class Delivery(BaseModel):
    notification: Notification
    subscription_id: str
    subscription: PushSubscription = Field(repr=False)
    attempts: int
