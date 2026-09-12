import re
import shlex
import tomllib
from pathlib import Path

from matoca_service.config import LineConfig, MerchantRegistry, RuntimeSettings

REPO_ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "browser-ui.yml"


def _workflow_run_commands(workflow: str) -> list[list[str]]:
    commands: list[list[str]] = []
    lines = workflow.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)- run:\s*(.*)$", lines[index])
        if match is None:
            index += 1
            continue

        indentation, value = match.groups()
        if value in {">", ">-", "|", "|-"}:
            block: list[str] = []
            index += 1
            while index < len(lines):
                line = lines[index]
                if line.strip() and len(line) - len(line.lstrip()) <= len(indentation):
                    break
                if line.strip():
                    block.append(line.strip())
                index += 1
            value = " ".join(block)
        else:
            index += 1

        commands.append(shlex.split(value))
    return commands


def _command_selects_marker(command: list[str], marker: str) -> bool:
    return any(command[index : index + 2] == ["-m", marker] for index in range(len(command) - 1))


def test_runtime_templates_never_contain_credentials() -> None:
    env_text = (REPO_ROOT / ".env.example").read_text()

    assert "access_token" not in env_text.lower()
    assert "refresh_token" not in env_text.lower()


def test_mutable_files_are_gitignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text().splitlines()

    assert {
        "state.json",
        "state.lock",
        "line_client.toml",
        "data/",
        ".env",
        "docs/superpowers/",
        ".superpowers/",
    } <= set(ignored)


def test_builtin_registry_contains_supported_sawayaka_merchant() -> None:
    registry = MerchantRegistry.load_builtin()

    assert registry.merchants["sawayaka"].name_ja == "炭焼きレストラン さわやか"
    assert registry.merchants["sawayaka"].liff_id == "2006055787-m6P6OJ38"


def test_builtin_registry_contains_la_ohana_yokohamahonmoku() -> None:
    registry = MerchantRegistry.load_builtin()

    merchant = registry.merchants["la_ohana_yokohamahonmoku"]
    assert merchant.name_ja == "ラ・オハナ 横浜本牧"
    assert merchant.liff_id == "2009221823-CiKhIxff"
    assert str(merchant.entry_url) == (
        "https://exclusive-mini.junbanmachi.jp/la-ohana-yokohamahonmoku/"
    )


def test_line_client_profile_is_loaded_separately(tmp_path: Path) -> None:
    profile = tmp_path / "line_client.toml"
    profile.write_text(
        "\n".join(
            [
                'host = "legy-jp.line-apps.com"',
                'application = "synthetic-app"',
                'locale = "ja_JP"',
                'protocol_version = "1"',
                'user_agent = "synthetic-agent"',
            ]
        ),
        encoding="utf-8",
    )

    config = LineConfig.from_toml(profile)

    assert config.application == "synthetic-app"
    assert config.locale == "ja_JP"


def test_runtime_settings_use_explicit_line_and_database_paths() -> None:
    settings = RuntimeSettings(_env_file=None)

    assert settings.line_client_file == Path("line_client.toml")
    assert settings.database_file == Path("data/matoca.db")
    assert not hasattr(settings, "config_file")


def test_package_contract_includes_web_templates_and_static_assets() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    targets = project["tool"]["hatch"]["build"]["targets"]
    source_static = "src/matoca_service/web/static"
    source_templates = "src/matoca_service/web/templates"

    assert targets["wheel"]["packages"] == ["src/matoca_service"]
    assert targets["wheel"]["artifacts"] == [
        f"/{source_static}/**",
        f"/{source_templates}/**",
    ]
    assert targets["sdist"]["force-include"] == {
        source_static: source_static,
        source_templates: source_templates,
    }
    assert (REPO_ROOT / source_static).is_dir()
    assert (REPO_ROOT / source_templates).is_dir()


def test_readme_documents_manual_console_behavior() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    for phrase in (
        "炭焼きレストラン さわやか",
        "ラ・オハナ 横浜本牧",
        "受付可能",
        "公式目安",
        "大人 2 人",
        "子ども 0 人",
        "手動操作",
        "店舗一覧は SQLite キャッシュから表示",
        "現在の順番待ちは Matoca API から独立して更新",
    ):
        assert phrase in readme


def test_browser_workflow_uses_frozen_uv_environment_and_matching_chromium() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    commands = _workflow_run_commands(workflow)

    assert "on: [push, pull_request]" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "uses: actions/checkout@v4" in workflow
    assert "uses: astral-sh/setup-uv@v6" in workflow
    assert ["uv", "sync", "--group", "browser", "--frozen"] in commands
    assert [
        "uv",
        "run",
        "--group",
        "browser",
        "python",
        "-m",
        "playwright",
        "install",
        "--with-deps",
        "chromium",
    ] in commands


def test_browser_workflow_overrides_default_marker_and_retains_failure_evidence() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    pytest_commands = [
        command for command in _workflow_run_commands(workflow) if "pytest" in command
    ]

    assert len(pytest_commands) == 1
    command = pytest_commands[0]
    assert _command_selects_marker(command, "browser")
    assert not _command_selects_marker(command, "not browser")
    assert "--tracing" in command
    assert command[command.index("--tracing") + 1] == "retain-on-failure"
    assert "--screenshot" in command
    assert command[command.index("--screenshot") + 1] == "only-on-failure"
    assert "--full-page-screenshot" in command

    artifact_step = re.search(
        r"(?ms)^\s+- if: failure\(\)\s+uses: actions/upload-artifact@v4\s+with:\s+"
        r"name: browser-test-results\s+path: test-results/\s*$",
        workflow,
    )
    assert artifact_step is not None
    assert workflow.count("uses: actions/upload-artifact@v4") == 1


def test_browser_workflow_is_credential_free_and_least_privilege() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    normalized = workflow.casefold()

    permissions = re.search(
        r"(?ms)^permissions:\s*\n(?P<body>(?:[ \t]+[^\n]+\n)+)",
        workflow,
    )
    assert permissions is not None
    assert [line.strip() for line in permissions.group("body").splitlines()] == ["contents: read"]
    assert re.search(r"\$\{\{\s*secrets(?:\.|\[)", normalized) is None
    for forbidden in (
        "state.json",
        "supervisor",
        "destructive_refresh",
        "destructive-refresh",
        "-m live",
        "192.168.",
        "matoca.biwako.io",
        "line-apps.com",
        "junbanmachi.jp",
    ):
        assert forbidden not in normalized


def test_readme_documents_isolated_browser_gate_and_failure_artifacts() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    for phrase in (
        "uv run pytest",
        "uv sync --group browser",
        "uv run --group browser python -m playwright install --with-deps chromium",
        "uv run --group browser pytest -m browser",
        "--tracing retain-on-failure",
        "--screenshot only-on-failure",
        "--full-page-screenshot",
        "test-results/",
        "uv run --group browser playwright show-trace test-results/<test-name>/trace.zip",
        "matching Chromium",
        "in-memory service",
        "external traffic",
        "real queue",
    ):
        assert phrase in readme
