import subprocess
from pathlib import Path


def test_queue_mutations_and_snapshot_refresh_preserve_frontend_queue_ownership() -> None:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            "node",
            str(Path(__file__).with_name("merchant_state.cjs")),
            str(root / "src/matoca_service/web/static/merchant.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
