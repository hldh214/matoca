from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]


def test_runtime_templates_never_contain_credentials() -> None:
    env_text = (REPO_ROOT / ".env.example").read_text()

    assert "access_token" not in env_text.lower()
    assert "refresh_token" not in env_text.lower()


def test_mutable_files_are_gitignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text().splitlines()

    assert {"state.json", "state.lock", "config.toml", ".env"} <= set(ignored)
