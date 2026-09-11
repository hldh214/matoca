import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[3] / "src/matoca_service/web/static"
MODULES = ("api", "shop-list", "queue-status", "join-form", "preferences")


@pytest.mark.parametrize("module", MODULES)
def test_console_module_exists_and_is_imported(module: str) -> None:
    assert (STATIC / f"{module}.js").is_file(), f"Missing console module: {module}"
    source = (STATIC / "merchant.js").read_text()
    assert re.search(rf"from [\'\"]\./{module}\.js[\'\"]", source)


def test_console_transport_is_isolated_and_dynamic_html_is_not_used() -> None:
    sources = {name: (STATIC / f"{name}.js").read_text() for name in (*MODULES, "merchant")}
    assert "/console" in sources["api"]
    for name, source in sources.items():
        assert "/snapshot" not in source, name
        assert "innerHTML" not in source, name
        assert "insertAdjacentHTML" not in source, name
        if name != "api":
            assert "/api/" not in source, name
            assert "/console" not in source, name


def test_console_interface_messages_are_japanese() -> None:
    for asset in STATIC.glob("*.js"):
        source = asset.read_text()
        assert "ブランド" not in source
        assert not re.search(
            r"[\'\"](?:Loading|Failed to|Error:|Please |Submit|Cancel|Settings)", source
        ), asset.name
