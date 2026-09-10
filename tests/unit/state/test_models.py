from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from matoca_service.state.models import AppState, LiffTokenState, LineState


def test_state_accepts_one_native_pair_and_per_liff_tokens() -> None:
    state = AppState(
        version=1,
        line=LineState(
            access_token="access-secret",
            refresh_token="refresh-secret",
            adid="device-id",
        ),
        liff_tokens={
            "2006055787-m6P6OJ38": LiffTokenState(
                access_token="liff-secret",
                expires_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
            )
        },
    )

    assert state.version == 1
    assert state.line.adid == "device-id"
    assert "2006055787-m6P6OJ38" in state.liff_tokens


def test_state_repr_does_not_contain_credentials() -> None:
    state = AppState(
        version=1,
        line=LineState(
            access_token="access-secret",
            refresh_token="refresh-secret",
            adid="device-id",
        ),
    )

    rendered = repr(state)

    assert "access-secret" not in rendered
    assert "refresh-secret" not in rendered


def test_state_validation_error_hides_invalid_credential_input() -> None:
    credential = "credential-that-must-not-leak"

    with pytest.raises(ValidationError) as error:
        LineState(access_token=credential, refresh_token="", adid="device-id")

    assert credential not in str(error.value)
