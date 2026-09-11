import subprocess
import tomllib
from pathlib import Path

from matoca_service.config import LineConfig, MerchantRegistry, RuntimeSettings

REPO_ROOT = Path(__file__).parents[2]


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
    result = subprocess.run(
        ["git", "ls-files", "src/matoca_service/web"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected = {
        "src/matoca_service/web/templates/dashboard.html",
        "src/matoca_service/web/templates/merchant.html",
        "src/matoca_service/web/static/api.js",
        "src/matoca_service/web/static/dashboard.css",
        "src/matoca_service/web/static/join-form.js",
        "src/matoca_service/web/static/merchant-selector.css",
        "src/matoca_service/web/static/merchant.css",
        "src/matoca_service/web/static/merchant.js",
        "src/matoca_service/web/static/preferences.js",
        "src/matoca_service/web/static/queue-status.js",
        "src/matoca_service/web/static/shop-list.js",
    }
    tracked_files = set(result.stdout.splitlines())
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel_packages = project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert wheel_packages == ["src/matoca_service"]
    assert expected <= tracked_files


def test_readme_documents_manual_console_behavior() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    for phrase in (
        "炭焼きレストラン さわやか",
        "ラ・オハナ 横浜本牧",
        "受付中のみ",
        "公式目安",
        "成人 2 人",
        "子供 0 人",
        "手動操作",
        "店舗一覧は SQLite キャッシュから表示",
        "現在の順番待ちは Matoca API から独立して更新",
    ):
        assert phrase in readme
