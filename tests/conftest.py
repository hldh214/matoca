import base64
import json
from collections.abc import Callable
from typing import Any

import pytest


@pytest.fixture
def jwt_factory() -> Callable[[dict[str, Any]], str]:
    def build(payload: dict[str, Any]) -> str:
        header = {"alg": "RS256", "typ": "JWT"}

        def encode(value: dict[str, Any]) -> str:
            raw = json.dumps(value, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        return f"{encode(header)}.{encode(payload)}.synthetic-signature"

    return build
