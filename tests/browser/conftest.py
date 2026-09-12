from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import pytest
import uvicorn
from playwright.sync_api import ConsoleMessage, Error, Page, Request, Route

from matoca_service.web.app import create_app

from .fake_service import BrowserFakeService

DEFAULT_VIEWPORT = (1440, 900)
SERVER_TIMEOUT_SECONDS = 10.0


def is_allowed_loopback_url(url: str, allowed_origin: str) -> bool:
    try:
        candidate = urlsplit(url)
        allowed = urlsplit(allowed_origin)
        allowed_port = allowed.port
        candidate_port = candidate.port
    except ValueError:
        return False
    if (
        allowed.scheme != "http"
        or allowed.hostname != "127.0.0.1"
        or allowed_port is None
        or allowed.username is not None
        or allowed.password is not None
        or allowed.path not in {"", "/"}
        or allowed.query
        or allowed.fragment
    ):
        return False
    return (
        candidate.scheme == allowed.scheme
        and candidate.hostname == allowed.hostname
        and candidate_port == allowed_port
        and candidate.username is None
        and candidate.password is None
    )


@dataclass
class BrowserDiagnostics:
    page_errors: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    request_failures: list[str] = field(default_factory=list)
    external_requests: list[str] = field(default_factory=list)

    def assert_clean(self) -> None:
        problems: list[str] = []
        if self.page_errors:
            problems.append(f"page errors: {self.page_errors!r}")
        if self.console_errors:
            problems.append(f"console errors: {self.console_errors!r}")
        if self.request_failures:
            problems.append(f"failed requests: {self.request_failures!r}")
        if self.external_requests:
            problems.append(f"external requests: {self.external_requests!r}")
        assert not problems, "Unexpected browser diagnostics:\n" + "\n".join(problems)


@pytest.fixture
def browser_service() -> BrowserFakeService:
    return BrowserFakeService()


@pytest.fixture
def browser_base_url(browser_service: BrowserFakeService) -> Generator[str]:
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind(("127.0.0.1", 0))
    listen_socket.listen(socket.SOMAXCONN)
    port = listen_socket.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(browser_service), log_level="warning"))
    thread_errors: list[BaseException] = []

    def run_server() -> None:
        try:
            server.run(sockets=[listen_socket])
        except BaseException as error:
            thread_errors.append(error)

    thread = threading.Thread(target=run_server, daemon=True, name="browser-fake-service")
    thread.start()
    wait_event = threading.Event()
    deadline = monotonic() + SERVER_TIMEOUT_SECONDS
    while not server.started and thread.is_alive() and monotonic() < deadline:
        wait_event.wait(timeout=min(0.05, deadline - monotonic()))

    try:
        if not server.started:
            detail = f": {thread_errors[0]!r}" if thread_errors else ""
            pytest.fail(f"browser test server did not start within timeout{detail}")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=SERVER_TIMEOUT_SECONDS)
        listen_socket.close()
        if thread.is_alive():
            pytest.fail("browser test server did not stop within timeout")
        if thread_errors:
            pytest.fail(f"browser test server failed: {thread_errors[0]!r}")


@pytest.fixture
def browser_context_args(
    browser_context_args: dict[str, Any],
    request: pytest.FixtureRequest,
) -> dict[str, Any]:
    args = dict(browser_context_args)
    viewport = DEFAULT_VIEWPORT
    callspec = getattr(request.node, "callspec", None)
    if callspec is not None and "viewport" in callspec.params:
        viewport = callspec.params["viewport"]
    args["viewport"] = {"width": viewport[0], "height": viewport[1]}
    return args


@pytest.fixture
def _browser_diagnostics() -> Generator[BrowserDiagnostics]:
    diagnostics = BrowserDiagnostics()
    yield diagnostics
    diagnostics.assert_clean()


@pytest.fixture
def safe_page(
    page: Page,
    browser_base_url: str,
    _browser_diagnostics: BrowserDiagnostics,
) -> Page:
    diagnostics = _browser_diagnostics

    def record_page_error(error: Error) -> None:
        diagnostics.page_errors.append(str(error))

    def record_console_error(message: ConsoleMessage) -> None:
        if message.type == "error":
            diagnostics.console_errors.append(message.text)

    def record_request_failure(request: Request) -> None:
        diagnostics.request_failures.append(f"{request.url}: {request.failure}")

    def guard(route: Route) -> None:
        if is_allowed_loopback_url(route.request.url, browser_base_url):
            route.continue_()
        else:
            diagnostics.external_requests.append(route.request.url)
            route.abort("blockedbyclient")

    page.on("pageerror", record_page_error)
    page.on("console", record_console_error)
    page.on("requestfailed", record_request_failure)
    page.route("**/*", guard)
    return page


@pytest.fixture
def assert_clean_browser(
    _browser_diagnostics: BrowserDiagnostics,
) -> Callable[[], None]:
    return _browser_diagnostics.assert_clean
