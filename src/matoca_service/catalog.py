import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from matoca_service.matoca.models import Shop


class MerchantCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refreshed_at: datetime
    shops: list[Shop]

    def fresh_for(self, now: datetime) -> bool:
        return now - self.refreshed_at < timedelta(hours=24)


class CatalogState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    merchants: dict[str, MerchantCatalog] = Field(default_factory=dict)


class ShopCatalogStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> CatalogState:
        if not self.path.exists():
            return CatalogState()
        return CatalogState.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, state: CatalogState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(state.model_dump(mode="json"), temporary_file, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
            temporary_path = None
            descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
