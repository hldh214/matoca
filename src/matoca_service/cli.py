from datetime import UTC, datetime

import typer

from matoca_service.config import RuntimeSettings
from matoca_service.line.jwt import validate_native_pair
from matoca_service.state.store import JsonStateStore

app = typer.Typer(no_args_is_help=True)
state_app = typer.Typer(no_args_is_help=True)
app.add_typer(state_app, name="state")


def _format_expiry(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


@state_app.command("status")
def state_status() -> None:
    settings = RuntimeSettings()
    state = JsonStateStore(settings.state_file).load()
    claims = validate_native_pair(state.line.access_token, state.line.refresh_token)
    family = f"{claims.rtid[:8]}...{claims.rtid[-5:]}"
    typer.echo("Native access token: valid")
    typer.echo(f"Access expiry: {_format_expiry(claims.access_expires_at)}")
    typer.echo("Refresh token: present")
    typer.echo(f"Refresh expiry: {_format_expiry(claims.refresh_expires_at)}")
    typer.echo(f"Credential family: {family}")
    typer.echo(f"Pending access report: {state.line.pending_access_report}")
