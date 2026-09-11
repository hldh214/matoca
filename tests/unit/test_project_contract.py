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
        "受付中のみ",
        "公式目安",
        "成人 2 人",
        "子供 0 人",
        "手動操作",
        "店舗一覧は SQLite キャッシュから表示",
        "現在の順番待ちは Matoca API から独立して更新",
    ):
        assert phrase in readme
