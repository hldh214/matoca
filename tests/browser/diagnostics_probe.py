from __future__ import annotations

import json
import os
import threading
import urllib.request
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser


def test_automatic_console_error_probe(safe_page: Any, browser_base_url: str) -> None:
    safe_page.goto(browser_base_url, wait_until="networkidle")
    safe_page.evaluate("console.error('automatic teardown probe')")


def test_call_failure_console_error_probe(safe_page: Any) -> None:
    safe_page.evaluate("console.error('call failure diagnostic')")
    pytest.fail("intentional call failure")


def test_page_close_order_probe(safe_page: Any, browser_base_url: str) -> None:
    marker = Path(os.environ["MATOCA_CLOSE_PROBE"])
    safe_page.goto(browser_base_url)

    def record_server_status(_page: Any) -> None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(browser_base_url, timeout=2) as response:
                result = str(response.status)
        except OSError as error:
            result = f"error: {error}"
        marker.write_text(result, encoding="utf-8")

    safe_page.on("close", record_server_status)


@pytest.fixture
def other_origin() -> Generator[str]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            body = b"synthetic alternate origin"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), "alternate-origin server did not stop"
        Path(os.environ["MATOCA_NETWORK_PROBE"]).write_text(json.dumps(requests), encoding="utf-8")


def test_popup_http_guard_probe(safe_page: Page, browser_base_url: str, other_origin: str) -> None:
    safe_page.goto(browser_base_url)
    with safe_page.expect_popup() as popup_info:
        safe_page.evaluate("url => window.open(url)", f"{other_origin}/popup-http-probe")
    popup_info.value.wait_for_load_state("domcontentloaded")


def test_websocket_guard_probe(safe_page: Page, browser_base_url: str, other_origin: str) -> None:
    safe_page.goto(browser_base_url)
    with safe_page.expect_popup() as popup_info:
        safe_page.evaluate("window.open('about:blank')")
    popup = popup_info.value
    popup.evaluate(
        "url => { window.probeSocket = new WebSocket(url); }",
        other_origin.replace("http:", "ws:") + "/websocket-probe",
    )
    popup.wait_for_function("window.probeSocket.readyState !== WebSocket.CONNECTING", timeout=5_000)


def test_additional_page_diagnostics_probe(safe_page: Page) -> None:
    if os.environ["MATOCA_PAGE_KIND"] == "popup":
        with safe_page.expect_popup() as popup_info:
            safe_page.evaluate("window.open('about:blank')")
        page = popup_info.value
    else:
        page = safe_page.context.new_page()
    if os.environ["MATOCA_DIAGNOSTIC_KIND"] == "console":
        page.evaluate("console.error('additional page console diagnostic')")
    else:
        page.evaluate(
            """() => new Promise(resolve => setTimeout(() => {
                resolve();
                throw new Error('additional page error diagnostic');
            }, 0))"""
        )
