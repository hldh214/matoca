import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineConfig(ConfigModel):
    host: str
    application: str
    locale: str
    protocol_version: str
    user_agent: str


class MerchantConfig(ConfigModel):
    name: str
    liff_id: str
    api_base_url: HttpUrl
    origin: HttpUrl
    entry_url: HttpUrl
    line_entry_url: str


class AppConfig(ConfigModel):
    line: LineConfig
    merchants: dict[str, MerchantConfig]

    @classmethod
    def from_toml(cls, path: Path) -> AppConfig:
        with path.open("rb") as config_file:
            return cls.model_validate(tomllib.load(config_file))


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MATOCA_", extra="ignore")

    config_file: Path = Path("./config.toml")
    state_file: Path = Path("./state.json")
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8080
