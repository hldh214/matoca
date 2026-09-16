import base64
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from matoca_service.state.models import VapidState
from matoca_service.state.store import JsonStateStore


class VapidKeys:
    def __init__(self, store: JsonStateStore) -> None:
        self.store = store

    def public_key(self) -> str | None:
        state = self.store.load()
        return state.vapid.public_key if state.vapid else None

    def enable(self, subject: str) -> str:
        parsed = urlsplit(subject)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("通知を有効にするにはHTTPSで開いてください")
        with self.store.locked():
            state = self.store.load()
            if state.vapid is None:
                key = ec.generate_private_key(ec.SECP256R1())
                private = key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                public = key.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
                state.vapid = VapidState(
                    private_key=base64.urlsafe_b64encode(private).decode().rstrip("="),
                    public_key=base64.urlsafe_b64encode(public).decode().rstrip("="),
                    subject=subject,
                )
                self.store.save(state)
            return state.vapid.public_key

    def credentials(self) -> VapidState | None:
        return self.store.load().vapid
