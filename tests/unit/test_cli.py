from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from matoca_service.cli import app
from matoca_service.state.models import AppState, LineState
from matoca_service.state.store import JsonStateStore

type JwtFactory = Callable[[dict[str, Any]], str]


def test_state_status_is_runnable_and_redacts_tokens(
    tmp_path: Path,
    jwt_factory: JwtFactory,
) -> None:
    access = jwt_factory(
        {
            "jti": "access-jti",
            "rtid": "family-jti",
            "aud": "LINE",
            "iat": 1_789_000_000,
            "exp": 1_789_604_800,
        }
    )
    refresh = jwt_factory(
        {
            "jti": "family-jti",
            "ati": "access-jti",
            "aud": "LINE",
            "iat": 1_789_000_000,
            "exp": 1_820_536_000,
        }
    )
    state_path = tmp_path / "state.json"
    JsonStateStore(state_path).save(
        AppState(
            line=LineState(
                access_token=access,
                refresh_token=refresh,
                adid="device-id",
            )
        )
    )

    result = CliRunner().invoke(
        app,
        ["state", "status"],
        env={"MATOCA_STATE_FILE": str(state_path)},
    )

    assert result.exit_code == 0
    assert "Native access token: valid" in result.output
    assert "family-jti" not in result.output
    assert access not in result.output
    assert refresh not in result.output
