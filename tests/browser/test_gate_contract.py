import shlex
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def test_browser_dependencies_and_marker_are_separate() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    browser = config["dependency-groups"]["browser"]
    assert any(item.startswith("playwright") for item in browser)
    assert any(item.startswith("pytest-playwright") for item in browser)

    pytest_config = config["tool"]["pytest"]["ini_options"]
    assert any(marker.startswith("browser:") for marker in pytest_config["markers"])

    addopts = shlex.split(pytest_config["addopts"])
    marker_indexes = [index for index, option in enumerate(addopts) if option == "-m"]
    assert addopts[marker_indexes[-1] + 1] == "not browser"


def test_browser_artifacts_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "test-results/" in ignored


@pytest.mark.browser
def test_browser_gate_sentinel() -> None:
    assert True
