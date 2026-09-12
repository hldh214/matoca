from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from matoca_service.service import PartyPreferences, QueueSubmission

from .conftest import is_allowed_loopback_url
from .fake_service import BrowserFakeService

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser
PROBE_PATH = Path(__file__).with_name("diagnostics_probe.py")


def run_diagnostics_probe(
    test_name: str,
    tmp_path: Path,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    probe_environment = os.environ.copy()
    if environment is not None:
        probe_environment.update(environment)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(PROBE_PATH),
            "-m",
            "browser",
            "-k",
            test_name,
            "-q",
            "-p",
            "no:cacheprovider",
            "--output",
            str(tmp_path / "probe-artifacts"),
            "--tracing=retain-on-failure",
            "--screenshot=only-on-failure",
            "--full-page-screenshot",
        ],
        cwd=PROBE_PATH.parents[2],
        env=probe_environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.asyncio
async def test_fake_service_mutates_only_in_memory() -> None:
    service = BrowserFakeService()
    assert await service.current_waiting("sawayaka") == []
    submission = QueueSubmission(
        shop_id=3272,
        adult_count=2,
        child_count=0,
        answer1=1,
    )

    waiting = await service.create_waiting("sawayaka", submission)

    assert waiting.id == 900000001
    assert waiting.shop_id == 3272
    assert waiting.adult_count == 2
    assert waiting.child_count == 0
    assert waiting.number == 101
    assert waiting.count == 8
    assert waiting.model_extra == {"waiting_time": {"minutes": 25, "is_more": False}}
    assert service.submissions == [submission]
    assert await service.current_waiting("sawayaka") == [waiting]

    await service.cancel_waiting("sawayaka", waiting.id)

    assert await service.current_waiting("sawayaka") == []
    assert service.submissions == [submission]


@pytest.mark.asyncio
async def test_fake_service_exposes_supported_merchants_and_shop_states() -> None:
    service = BrowserFakeService()

    merchants = service.list_merchants()
    console = await service.merchant_console("sawayaka")
    detail = await service.shop_detail("sawayaka", 3272)

    assert [(merchant.key, merchant.name) for merchant in merchants] == [
        ("sawayaka", "炭焼きレストラン さわやか"),
        ("la_ohana_yokohamahonmoku", "ラ・オハナ 横浜本牧"),
    ]
    assert all(
        merchant.cover_image_url is None or merchant.cover_image_url.startswith("/static/")
        for merchant in merchants
    )
    assert {shop.status for shop in console.shops} == {
        "available",
        "closed",
        "suspended",
        "stale",
    }
    assert all(
        shop.image_url is None or shop.image_url.startswith("/static/") for shop in console.shops
    )
    assert console.available_count == 1
    assert console.total_count == 4
    assert detail.model_dump(exclude_unset=True) == {
        "id": 3272,
        "name": "炭焼きレストラン さわやか",
        "sub_name": "浜松テスト店",
        "lat": 34.7,
        "lng": 137.7,
        "current_waiting": 8,
        "forms": {
            "min_adult": 2,
            "max_adult": 5,
            "min_child": 1,
            "max_child": 3,
            "confirm_items": [
                {
                    "enable": True,
                    "title": "注意事項を確認しましたか",
                    "sub_items": [
                        {
                            "enable": True,
                            "disabled": False,
                            "sub_item_index": 1,
                            "text": "確認しました",
                        }
                    ],
                }
            ],
        },
        "waiting_time": {"minutes": 25, "is_more": False},
        "is_issuable": True,
        "is_open": True,
    }


@pytest.mark.asyncio
async def test_fake_service_preferences_and_refresh_stay_local() -> None:
    service = BrowserFakeService()
    preferences = PartyPreferences(default_adult_count=3, default_child_count=1)

    assert await service.update_party_preferences(preferences) == preferences
    assert await service.party_preferences() == preferences
    snapshot = await service.merchant_snapshot("sawayaka", force_catalog=True)

    assert service.refresh_calls == 1
    assert snapshot.merchant.key == "sawayaka"
    assert snapshot.waiting == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:43127/", True),
        ("http://127.0.0.1:43127/static/dashboard.css?version=1", True),
        ("http://example.test:43127/", False),
        ("http://192.168.10.103:48173/", False),
        ("http://127.0.0.1:43128/", False),
        ("https://127.0.0.1:43127/", False),
    ],
)
def test_loopback_allowlist_requires_exact_fixture_origin(url: str, expected: bool) -> None:
    assert is_allowed_loopback_url(url, "http://127.0.0.1:43127") is expected


def test_isolated_home_loads_without_external_requests(
    safe_page: Page,
    browser_base_url: str,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    response = safe_page.goto(browser_base_url, wait_until="networkidle")

    assert response is not None and response.status == 200
    expect(safe_page.get_by_role("heading", name="利用する加盟店を選ぶ")).to_be_visible()
    assert_clean_browser()


def test_console_error_without_helper_fails_automatically(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "probe-artifacts"
    result = run_diagnostics_probe(
        "test_automatic_console_error_probe",
        tmp_path,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "automatic teardown probe" in output
    assert output.count("ERROR at teardown") == 1
    assert "1 error" in output
    screenshots = list(artifact_dir.rglob("*.png"))
    traces = list(artifact_dir.rglob("*.zip"))
    assert len(screenshots) == len(traces) == 1, (screenshots, traces, output)
    assert screenshots[0].stat().st_size > 0
    assert traces[0].stat().st_size > 0


def test_child_probe_preserves_parent_artifacts(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "parent-artifacts"
    artifact_dir.mkdir()
    artifact = artifact_dir / "parent-failure.png"
    artifact.write_bytes(b"previous parent artifact")

    result = run_diagnostics_probe(
        "test_automatic_console_error_probe",
        tmp_path,
        environment={"PYTEST_ADDOPTS": f"--output={artifact_dir}"},
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert artifact.is_file(), "child pytest deleted a parent failure artifact"
    assert artifact.read_bytes() == b"previous parent artifact"


def test_call_failure_reports_diagnostics_without_second_teardown_error(tmp_path: Path) -> None:
    result = run_diagnostics_probe("test_call_failure_console_error_probe", tmp_path)
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "intentional call failure" in output
    assert "browser diagnostics" in output
    assert "call failure diagnostic" in output
    assert "ERROR at teardown" not in output


def test_safe_page_closes_before_loopback_server(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "server-status.txt"

    result = run_diagnostics_probe(
        "test_page_close_order_probe",
        tmp_path,
        environment={"MATOCA_CLOSE_PROBE": str(marker)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8") == "200"
    assert list((tmp_path / "probe-artifacts").rglob("*.png")) == []
    assert list((tmp_path / "probe-artifacts").rglob("*.zip")) == []


@pytest.mark.parametrize(
    ("probe", "diagnostic", "attempt"),
    [
        ("test_popup_http_guard_probe", "external requests:", "/popup-http-probe"),
        ("test_websocket_guard_probe", "blocked WebSockets:", "/websocket-probe"),
    ],
)
def test_context_blocks_alternate_origin_without_contacting_it(
    tmp_path: Path, probe: str, diagnostic: str, attempt: str
) -> None:
    marker = tmp_path / "network-requests.json"
    result = run_diagnostics_probe(
        probe,
        tmp_path,
        environment={"MATOCA_NETWORK_PROBE": str(marker)},
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "ERROR at teardown" in output
    assert diagnostic in output, output
    assert attempt in output, output
    assert json.loads(marker.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("page_kind", ["popup", "new_page"])
@pytest.mark.parametrize("diagnostic_kind", ["console", "error"])
def test_additional_context_pages_fail_on_unexpected_diagnostics(
    tmp_path: Path, page_kind: str, diagnostic_kind: str
) -> None:
    result = run_diagnostics_probe(
        "test_additional_page_diagnostics_probe",
        tmp_path,
        environment={
            "MATOCA_PAGE_KIND": page_kind,
            "MATOCA_DIAGNOSTIC_KIND": diagnostic_kind,
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "ERROR at teardown" in output
    assert f"additional page {diagnostic_kind} diagnostic" in output


def test_service_workers_are_blocked(safe_page: Page, browser_base_url: str) -> None:
    safe_page.goto(browser_base_url)

    registered = safe_page.evaluate(
        """async () => Boolean(await navigator.serviceWorker.register(
            '/static/shop-list.js', {type: 'module'}))"""
    )

    assert registered is False
    assert safe_page.context.service_workers == []
