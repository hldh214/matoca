import tomllib
from importlib.resources import files
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

    @classmethod
    def from_toml(cls, path: Path) -> LineConfig:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
        return cls.model_validate(data.get("line", data))


class MerchantConfig(ConfigModel):
    name: str
    liff_id: str
    api_base_url: HttpUrl
    origin: HttpUrl
    entry_url: HttpUrl
    line_entry_url: str
    cover_image_url: HttpUrl | None = None

    @property
    def name_ja(self) -> str:
        return self.name


class MerchantRegistry(ConfigModel):
    merchants: dict[str, MerchantConfig]

    @classmethod
    def load_builtin(cls) -> MerchantRegistry:
        resource = files("matoca_service").joinpath("merchant_registry.toml")
        return cls.model_validate(tomllib.loads(resource.read_text(encoding="utf-8")))


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MATOCA_", extra="ignore")

    line_client_file: Path = Path("./line_client.toml")
    state_file: Path = Path("./state.json")
    shop_cache_file: Path = Path("./shop_catalog.json")
    database_file: Path = Path("./data/matoca.db")
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 48173
