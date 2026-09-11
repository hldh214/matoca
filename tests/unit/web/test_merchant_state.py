import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "initial_filter_counts",
        "unknown_queue_blocks_join",
        "catalog_preserves_queue",
        "stale_waiting_cannot_restore_cancelled_queue",
        "settings_only_change_next_dialog",
        "live_detail_clamps_and_hides_child",
        "sole_enabled_confirmation_is_selected",
        "unsupported_confirmations_block_submit",
        "representable_choices_require_selection",
        "polling_and_transport_are_safe",
        "upstream_text_and_errors_are_safe",
        "detail_failure_never_submits_cached_form",
        "stale_preferences_cannot_overwrite_saved_defaults",
        "malformed_confirmations_are_not_silently_ignored",
    ],
)
def test_merchant_console_behavior(case: str) -> None:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            "node",
            "--experimental-vm-modules",
            "--no-warnings",
            str(Path(__file__).with_name("merchant_state.cjs")),
            str(root / "src/matoca_service/web/static/merchant.js"),
            case,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
