import os
import shlex
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def test_browser_dependencies_and_marker_are_separate() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    browser = config["dependency-groups"]["browser"]
    assert any(item.startswith("playwright") for item in browser)
    assert any(item.startswith("pytest-playwright") for item in browser)
    assert config["tool"]["uv"]["default-groups"] == ["dev"]

    pytest_config = config["tool"]["pytest"]["ini_options"]
    assert any(marker.startswith("browser:") for marker in pytest_config["markers"])

    addopts = shlex.split(pytest_config["addopts"])
    marker_indexes = [index for index, option in enumerate(addopts) if option == "-m"]
    assert addopts[marker_indexes[-1] + 1] == "not browser"


def test_browser_artifacts_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "test-results/" in ignored


def test_browser_tests_collect_without_browser_dependencies() -> None:
    environment = os.environ.copy()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-group",
            "browser",
            "python",
            "-m",
            "pytest",
            "-p",
            "tests.browser.no_playwright_import",
            "-p",
            "pytest_asyncio.plugin",
            "tests/browser/test_harness.py",
            "--collect-only",
            "-m",
            "browser",
            "-q",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "playwright" not in (result.stdout + result.stderr).lower()


@pytest.mark.browser
def test_browser_gate_sentinel() -> None:
    assert True
