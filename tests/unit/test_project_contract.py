import tomllib
from pathlib import Path

import yaml

from matoca_service.config import LineConfig, MerchantRegistry, RuntimeSettings

REPO_ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "browser-ui.yml"


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


def test_browser_workflow_has_exact_safe_job_contract() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert workflow == {
        "name": "Browser UI",
        "on": ["push", "pull_request"],
        "permissions": {"contents": "read"},
        "jobs": {
            "browser": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {"uses": "astral-sh/setup-uv@v6"},
                    {"run": "uv sync --group browser --frozen"},
                    {
                        "run": "uv run --group browser python -m playwright install "
                        "--with-deps chromium"
                    },
                    {
                        "name": "Run isolated browser tests",
                        "shell": "bash",
                        "run": """mkdir -p test-results
set +e
set -o pipefail
uv run --group browser pytest -m browser \\
  --tracing retain-on-failure \\
  --screenshot only-on-failure \\
  --full-page-screenshot 2>&1 | tee test-results/pytest.log
status=${PIPESTATUS[0]}
if (( status != 0 )); then
  echo \"::group::Browser pytest failure\"
  tail -n 40 test-results/pytest.log
  echo \"::endgroup::\"
  while IFS= read -r line; do
    line=${line//'%'/'%25'}
    line=${line//$'\\r'/'%0D'}
    line=${line//$'\\n'/'%0A'}
    echo \"::error title=Browser pytest::${line}\"
  done < <(tail -n 40 test-results/pytest.log)
fi
exit \"$status\"
""",
                    },
                    {
                        "if": "failure()",
                        "uses": "actions/upload-artifact@v4",
                        "with": {
                            "name": "browser-test-results",
                            "path": "test-results/",
                        },
                    },
                ],
            }
        },
    }


def test_yaml_parser_is_a_direct_development_dependency() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "pyyaml>=6.0.3,<7" in project["dependency-groups"]["dev"]


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
