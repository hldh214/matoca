import os
import urllib.request
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.browser


def test_automatic_console_error_probe(safe_page: Any) -> None:
    safe_page.evaluate("console.error('automatic teardown probe')")


def test_call_failure_console_error_probe(safe_page: Any) -> None:
    safe_page.evaluate("console.error('call failure diagnostic')")
    pytest.fail("intentional call failure")


def test_page_close_order_probe(safe_page: Any, browser_base_url: str) -> None:
    marker = Path(os.environ["MATOCA_CLOSE_PROBE"])

    def record_server_status(_page: Any) -> None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(browser_base_url, timeout=2) as response:
                result = str(response.status)
        except OSError as error:
            result = f"error: {error}"
        marker.write_text(result, encoding="utf-8")

    safe_page.on("close", record_server_status)
