import sys
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec


class PlaywrightImportBlocker(MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> ModuleSpec | None:
        del path, target
        if fullname == "playwright" or fullname.startswith("playwright."):
            raise ModuleNotFoundError("playwright import blocked by collection contract")
        return None


sys.meta_path.insert(0, PlaywrightImportBlocker())
