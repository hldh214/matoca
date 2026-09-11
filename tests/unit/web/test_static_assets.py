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


def test_mobile_queue_reserves_height_and_contains_long_content() -> None:
    css = (STATIC / "merchant.css").read_text()
    mobile = css.split("@media (max-width: 760px)")[1]
    queue = re.search(r"\.queue-band\s*\{([^}]+)\}", mobile)
    assert queue is not None
    declarations = dict(
        (key.strip(), value.strip())
        for key, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", queue.group(1))
    )
    assert re.fullmatch(r"[\d.]+(?:px|rem)", declarations["height"]), (
        "Queue transitions must not change the mobile band's reserved height"
    )
    assert declarations.get("grid-template-rows") == "auto minmax(0, 1fr)"
    content = re.findall(r"\.queue-content\s*\{([^}]+)\}", css)
    assert any("overflow: auto" in rule and "min-height: 0" in rule for rule in content), (
        "Long queue/error content must scroll inside the band instead of overlapping the list"
    )
