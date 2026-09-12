# Browser UI Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate, deterministic Playwright browser gate that verifies the Japanese Web UI with synthetic data and cannot reach real LINE or Matoca services.

**Architecture:** Pytest starts the real FastAPI application with an injected in-memory service on a temporary loopback socket. Playwright drives the real templates, CSS, JavaScript modules, and API routes while a browser-level route guard rejects every non-loopback request. Browser tests use behavior and geometry assertions, retain diagnostics on failure, and remain excluded from normal `uv run pytest` runs.

**Tech Stack:** Python 3.14, uv dependency groups, pytest, pytest-playwright, Playwright Chromium, FastAPI, Uvicorn, GitHub Actions.

**Spec:** `docs/designs/2026-09-11-browser-ui-testing-design.md`

## Global Constraints

- Browser tests run separately with the `browser` pytest marker; default `uv run pytest` excludes them.
- Tests bind only to an unused `127.0.0.1` port and inject a fake service into `create_app()`.
- Tests never read `state.json`, start the collection coordinator, contact LINE/Matoca, or use Supervisor.
- Fake queue creation and cancellation mutate only in-memory test state.
- Every non-loopback browser request is aborted and fails the test.
- Unexpected page errors and error-level browser console messages fail the test.
- Test the `1440x900`, `390x844`, and `320x568` viewports.
- Verify behavior and geometry; do not commit pixel screenshot baselines.
- Retain screenshots and traces only on failure under ignored `test-results/`.
- All visible application copy remains Japanese and uses `加盟店`, not `ブランド`.
- Use uv-managed Python and dependencies; do not add a Node project or npm lockfile.

---

### Task 1: Separate Browser Test Gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.gitignore`
- Create: `tests/browser/__init__.py`
- Create: `tests/browser/test_gate_contract.py`

**Interfaces:**
- Consumes: existing pytest configuration and uv dependency groups.
- Produces: the `browser` dependency group, registered `browser` marker, default marker expression `not browser`, ignored `test-results/`, and a collection contract that proves ordinary pytest excludes browser tests.

- [ ] **Step 1: Write failing gate-contract tests**

Create `tests/browser/test_gate_contract.py` with repository-level assertions that parse
`pyproject.toml` using `tomllib` and read `.gitignore`:

```python
from pathlib import Path
import tomllib

ROOT = Path(__file__).parents[2]


def test_browser_dependencies_and_marker_are_separate() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    browser = config["dependency-groups"]["browser"]
    assert any(item.startswith("playwright") for item in browser)
    assert any(item.startswith("pytest-playwright") for item in browser)
    pytest_config = config["tool"]["pytest"]["ini_options"]
    assert "browser:" in pytest_config["markers"]
    assert pytest_config["addopts"].split().count("not") == 0
    assert pytest_config["addopts"].endswith("-m 'not browser'")


def test_browser_artifacts_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "test-results/" in ignored
```

Add a sentinel browser test in the same module:

```python
import pytest


@pytest.mark.browser
def test_browser_gate_sentinel() -> None:
    assert True
```

- [ ] **Step 2: Verify the contract fails for missing configuration**

Run:

```bash
uv run pytest tests/browser/test_gate_contract.py -m "not browser" -v
```

Expected: the configuration assertions fail because the browser dependency group,
marker exclusion, and ignored artifact directory do not exist.

- [ ] **Step 3: Add the browser dependency group and pytest configuration**

Add compatible bounded dependencies and configuration:

```toml
[dependency-groups]
browser = [
    "playwright>=1.60,<2",
    "pytest-playwright>=0.7,<1",
]

[tool.pytest.ini_options]
addopts = "-ra --strict-config --strict-markers -m 'not browser'"
markers = [
    "browser: runs a synthetic Chromium UI test and requires the browser dependency group",
    "live: calls the real LINE service and is skipped by default",
    "destructive_refresh: rotates the configured native LINE credentials",
]
```

Append `test-results/` to `.gitignore`, create the empty package file, and run `uv lock`
to update `uv.lock`. Do not install a browser during ordinary dependency resolution.

- [ ] **Step 4: Prove default and explicit selection semantics**

Run:

```bash
uv run pytest tests/browser/test_gate_contract.py -v
uv run --group browser pytest tests/browser/test_gate_contract.py -m browser -v
uv run pytest --collect-only -q
```

Expected: ordinary execution runs only the two contract tests and deselects the sentinel;
explicit browser selection runs only the sentinel; normal collection does not import
Playwright from non-browser tests.

- [ ] **Step 5: Commit the separate gate**

```bash
git add pyproject.toml uv.lock .gitignore tests/browser
git commit -m "test: define separate browser UI gate"
```

---

### Task 2: Isolated Browser Server And Safety Harness

**Files:**
- Create: `tests/browser/fake_service.py`
- Create: `tests/browser/conftest.py`
- Create: `tests/browser/test_harness.py`

**Interfaces:**
- Consumes: `create_app(service=...)`, `DashboardService`, `MerchantSummary`, `MerchantConsoleData`, `Shop`, `Waiting`, `PartyPreferences`, and `QueueSubmission`.
- Produces: `BrowserFakeService`, `browser_base_url: str`, `safe_page: Page`, `browser_service: BrowserFakeService`, and `assert_clean_browser()` fixtures/helpers for later workflow tests.

- [ ] **Step 1: Write failing fake-service behavior tests**

Create browser-marked tests in `tests/browser/test_harness.py` that instantiate
`BrowserFakeService` and assert:

```python
@pytest.mark.browser
@pytest.mark.asyncio
async def test_fake_service_mutates_only_in_memory() -> None:
    service = BrowserFakeService()
    assert await service.current_waiting("sawayaka") == []
    waiting = await service.create_waiting(
        "sawayaka",
        QueueSubmission(shop_id=3272, adult_count=2, child_count=0, answer1=1),
    )
    assert waiting.id == 900000001
    assert service.submissions == [
        QueueSubmission(shop_id=3272, adult_count=2, child_count=0, answer1=1)
    ]
    await service.cancel_waiting("sawayaka", waiting.id)
    assert await service.current_waiting("sawayaka") == []
```

Also assert `list_merchants()` returns the exact supported Japanese names and
`merchant_console("sawayaka")` returns at least one each of available, closed,
suspended, and stale shops with local or absent images.

- [ ] **Step 2: Verify RED because the harness does not exist**

Run:

```bash
uv run --group browser pytest tests/browser/test_harness.py -m browser -v
```

Expected: collection fails because `BrowserFakeService` and fixtures are undefined.

- [ ] **Step 3: Implement the in-memory service**

Implement every `DashboardService` protocol method without importing runtime settings.
Use fixed UTC timestamps and deterministic IDs. The available shop detail must include:

```python
Shop.model_validate(
    {
        "id": 3272,
        "name": "炭焼きレストラン さわやか",
        "sub_name": "浜松テスト店",
        "lat": 34.7,
        "lng": 137.7,
        "current_waiting": 8,
        "is_open": True,
        "is_issuable": True,
        "forms": {
            "min_adult": 1,
            "max_adult": 6,
            "min_child": 0,
            "max_child": 4,
            "confirm_items": [
                {
                    "enable": True,
                    "title": "注意事項を確認しましたか",
                    "sub_items": [
                        {"enable": True, "disabled": False, "sub_item_index": 1, "text": "確認しました"}
                    ],
                }
            ],
        },
        "waiting_time": {"minutes": 25, "is_more": False},
    }
)
```

`create_waiting()` records the exact submission and creates a `Waiting` with shop ID,
party counts, queue number, count, and a synthetic waiting-time extra field.
`cancel_waiting()` removes only a matching in-memory waiting entry.
`merchant_snapshot()` only increments `refresh_calls`; it performs no collection.

- [ ] **Step 4: Implement the loopback server and safe-page fixtures**

In `tests/browser/conftest.py`:

- bind an IPv4 socket to `("127.0.0.1", 0)` before starting Uvicorn;
- construct `uvicorn.Server(uvicorn.Config(create_app(service), log_level="warning"))`;
- run `server.run(sockets=[socket])` in a daemon thread;
- use a `threading.Event`/bounded condition loop keyed to `server.started`, not a fixed sleep;
- yield `http://127.0.0.1:<port>` and always set `server.should_exit = True`, join the
  thread, and fail if it remains alive;
- override `browser_context_args` with the required viewport per parametrized test;
- attach `pageerror`, error-level `console`, and `requestfailed` collectors;
- install `page.route("**/*", guard)` before navigation; allow only the fixture server's
  exact scheme/host/port and abort all other requests while recording their URL;
- expose `assert_clean_browser()` that fails with collected page errors, console errors,
  unexpected failures, or external requests.

Do not allow `example.test`, data collection hosts, or the deployed LAN URL.

- [ ] **Step 5: Add and pass an actual Chromium smoke test**

Add:

```python
@pytest.mark.browser
def test_isolated_home_loads_without_external_requests(
    safe_page: Page,
    browser_base_url: str,
    assert_clean_browser: Callable[[], None],
) -> None:
    response = safe_page.goto(browser_base_url, wait_until="networkidle")
    assert response is not None and response.status == 200
    expect(safe_page.get_by_role("heading", name="利用する加盟店を選ぶ")).to_be_visible()
    assert_clean_browser()
```

Run outside a process-restricted sandbox:

```bash
uv run --group browser pytest tests/browser/test_harness.py -m browser -v
```

Expected: fake-service and real Chromium smoke tests pass without any external request.

- [ ] **Step 6: Commit the safety harness**

```bash
git add tests/browser/fake_service.py tests/browser/conftest.py tests/browser/test_harness.py
git commit -m "test: isolate browser UI from production services"
```

---

### Task 3: Merchant Console Browser Workflows

**Files:**
- Create: `tests/browser/test_merchant_console.py`
- Modify: `tests/browser/fake_service.py`

**Interfaces:**
- Consumes: Task 2 fixtures and the real browser routes `/`, `/merchants/sawayaka`, `/api/preferences`, and `/api/merchants/sawayaka/*`.
- Produces: end-to-end coverage for selector navigation, filters/search, settings, join, current queue, cancellation, manual refresh, and payload recording.

- [ ] **Step 1: Write failing selector and catalog workflow**

Use accessible roles/text rather than CSS implementation details:

```python
@pytest.mark.browser
def test_selects_merchant_and_filters_shops(safe_page, browser_base_url, assert_clean_browser):
    safe_page.goto(browser_base_url)
    expect(safe_page.get_by_text("炭焼きレストラン さわやか", exact=True)).to_be_visible()
    expect(safe_page.get_by_text("ラ・オハナ 横浜本牧", exact=True)).to_be_visible()
    safe_page.get_by_role("link", name=re.compile("炭焼きレストラン さわやか")).click()
    expect(safe_page.get_by_role("button", name="受付可能")).to_have_attribute("aria-pressed", "true")
    expect(safe_page.locator(".shop-row")).to_have_count(1)
    expect(safe_page.locator("#available-count")).to_have_text("1")
    expect(safe_page.locator("#total-count")).to_have_text("4")
    expect(safe_page.get_by_text("8組")).to_be_visible()
    expect(safe_page.get_by_text("25分")).to_be_visible()
    safe_page.get_by_role("button", name="すべて").click()
    expect(safe_page.locator(".shop-row")).to_have_count(4)
    safe_page.locator("#shop-search").fill("休業")
    expect(safe_page.locator(".shop-row")).to_have_count(1)
    assert_clean_browser()
```

- [ ] **Step 2: Run and verify RED against incomplete fixture/UI expectations**

Run the single test. Expected: fail until the fake catalog names/statuses and exact
accessible locators match the real rendered interface. Correct the fixture, not the
production UI, unless the browser exposes an actual accessibility defect.

- [ ] **Step 3: Write the settings-to-next-dialog workflow test**

Open the join dialog first and assert defaults `2/0`. While it is open, save settings
`3/1` through the header dialog and prove the already-open join form remains `2/0`.
Close/reopen the join form and prove it becomes `3/1`. Assert the fake service preferences
equal `PartyPreferences(default_adult_count=3, default_child_count=1)`.

If native modal dialogs prevent opening settings simultaneously, express the same
contract by opening join at `2/0`, closing it, saving `3/1`, and proving the next opening
uses `3/1`; retain the existing Node harness as the race-level proof that a live open form
is not mutated by an asynchronous save.

- [ ] **Step 4: Write the synthetic join and cancellation workflow test**

From an available shop:

- open the join dialog and wait for live detail;
- increment adults once and children once;
- assert the single enabled confirmation is selected;
- click `この内容で順番待ちを申し込む`;
- assert `browser_service.submissions[-1]` contains shop `3272`, adult `3`, child `1`,
  `answer1=1`, and no location fields from the browser;
- assert the queue band shows the synthetic number and disables all join buttons;
- open `取消`, confirm `順番待ちを取り消す`, then assert the queue band reports no
  current waiting and join is enabled again.

All writes target only the injected fake service.

- [ ] **Step 5: Write the manual-refresh boundary test**

Click `最新情報に更新`, wait until the button is no longer busy, and assert
`browser_service.refresh_calls == 1`. Advance or wait through one browser timer interval
with Playwright clock support and prove automatic refresh changes only console/waiting
read counters while `refresh_calls` remains `1`.

- [ ] **Step 6: Run the workflow suite to GREEN**

Run outside the restricted agent sandbox:

```bash
uv run --group browser pytest tests/browser/test_merchant_console.py -m browser -v \
  --tracing retain-on-failure --screenshot only-on-failure --full-page-screenshot
```

Expected: all selector, catalog, settings, queue, cancel, and refresh workflows pass;
`test-results/` remains empty or contains no retained successful-test artifacts.

- [ ] **Step 7: Commit the workflows**

```bash
git add tests/browser/fake_service.py tests/browser/test_merchant_console.py
git commit -m "test: cover merchant console browser workflows"
```

---

### Task 4: Responsive Geometry And Failure Diagnostics

**Files:**
- Create: `tests/browser/geometry.py`
- Create: `tests/browser/test_responsive_layout.py`
- Modify: `tests/browser/conftest.py`

**Interfaces:**
- Consumes: Task 2 safe browser fixtures and Task 3 deterministic console states.
- Produces: `box(locator) -> Rect`, `assert_no_overlap(a, b)`, `assert_inside_viewport(locator, page)`, viewport matrix coverage, and verified failure artifact behavior.

- [ ] **Step 1: Write failing geometry-helper unit tests**

Define immutable `Rect(x, y, width, height)` and tests for edge-touching, overlap,
containment, and zero-area elements:

```python
def test_rectangles_overlap_only_when_their_areas_intersect() -> None:
    assert Rect(0, 0, 10, 10).overlaps(Rect(9, 9, 10, 10)) is True
    assert Rect(0, 0, 10, 10).overlaps(Rect(10, 0, 10, 10)) is False
```

`assert_inside_viewport` must compare left/right against viewport width and reject a
visible element with a zero-sized box.

- [ ] **Step 2: Verify RED and implement minimal geometry helpers**

Run the helper tests before and after adding `geometry.py`. Expected RED: missing module;
expected GREEN: all pure geometry cases pass without launching Chromium.

- [ ] **Step 3: Write the parametrized responsive browser test**

Parametrize `viewport` over `(1440, 900)`, `(390, 844)`, and `(320, 568)`. For each:

- load the populated Sawayaka console and wait for four rows under `すべて`;
- assert `document.documentElement.scrollWidth <= document.documentElement.clientWidth`;
- assert header, queue band, toolbar, counts, shop list, and visible action buttons remain
  horizontally inside the viewport;
- assert header does not overlap the queue band, queue band does not overlap the toolbar,
  toolbar/counts do not overlap the first row, and row content does not cover its action;
- record the queue band's bounding height with no active queue;
- create a synthetic waiting through the UI and assert the queue band height is unchanged;
- open settings and join/cancel dialogs in reachable states and assert each visible dialog
  is within the viewport and internally scrollable rather than causing horizontal overflow.

- [ ] **Step 4: Prove the layout test catches a real defect**

Temporarily inject test-only CSS with `page.add_style_tag(content=".queue-band{height:auto!important}")`
before changing queue state. Run the dedicated negative test and require its height
assertion to fail. Remove the injected-defect test after recording RED evidence; do not
weaken production assertions or commit a deliberately failing test.

- [ ] **Step 5: Verify failure diagnostics deliberately**

Run one browser test with a temporary failing assertion and the documented artifact
flags. Confirm `test-results/` contains a screenshot and trace archive. Remove the
temporary failure, rerun the real responsive suite, and confirm all tests pass. Record
the exact artifact filenames in the implementation report; do not commit artifacts.

- [ ] **Step 6: Commit responsive coverage**

```bash
git add tests/browser/geometry.py tests/browser/test_responsive_layout.py tests/browser/conftest.py
git commit -m "test: verify responsive console geometry"
```

---

### Task 5: CI, Documentation, And Final Gate

**Files:**
- Create: `.github/workflows/browser-ui.yml`
- Modify: `README.md`
- Modify: `tests/unit/test_project_contract.py`

**Interfaces:**
- Consumes: the standalone browser command and artifact layout from Tasks 1–4.
- Produces: a credential-free browser CI job and contributor-facing install, run, and trace instructions.

- [ ] **Step 1: Write failing project-contract tests**

Extend `tests/unit/test_project_contract.py` to parse the workflow with text/structured
checks and assert:

- workflow exists and uses `astral-sh/setup-uv`;
- it runs `uv sync --group browser`;
- it runs `uv run --group browser python -m playwright install --with-deps chromium`;
- it runs only `pytest -m browser` with retain-on-failure trace and failure screenshot;
- it uploads `test-results/` only on failure;
- it contains no secret interpolation, deployed host, `state.json`, Supervisor, live, or
  destructive-refresh commands;
- README documents normal tests, browser installation, standalone command, artifact
  location, `playwright show-trace`, matching-browser reinstall after upgrades, and the
  synthetic no-real-queue guarantee.

- [ ] **Step 2: Verify RED because workflow/docs are absent**

Run:

```bash
uv run pytest tests/unit/test_project_contract.py -v
```

Expected: new contract tests fail for missing workflow and browser documentation.

- [ ] **Step 3: Implement the GitHub Actions browser job**

Create a least-privilege workflow triggered by pushes and pull requests:

```yaml
name: Browser UI
on: [push, pull_request]
permissions:
  contents: read
jobs:
  browser:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --group browser --frozen
      - run: uv run --group browser python -m playwright install --with-deps chromium
      - run: >-
          uv run --group browser pytest -m browser
          --tracing retain-on-failure
          --screenshot only-on-failure
          --full-page-screenshot
      - if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: browser-test-results
          path: test-results/
```

Do not add secrets or point the job at a deployed URL.

- [ ] **Step 4: Document local browser testing**

Add a concise README section with the install/run commands, explain the separate marker,
state that the tests use an injected in-memory service and block external traffic, and
show:

```bash
uv run --group browser playwright show-trace test-results/<test-name>/trace.zip
```

Mention that Playwright upgrades require reinstalling its matching Chromium build.

- [ ] **Step 5: Run focused contracts and normal quality gate**

Run:

```bash
uv run pytest tests/unit/test_project_contract.py -v
uv run pytest
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests/browser
uv build
git diff --check
```

Expected: normal tests pass without launching Chromium; formatting, lint, typing, build,
and diff checks pass.

- [ ] **Step 6: Run the standalone browser quality gate**

Run outside process-restricted sandboxes:

```bash
uv run --group browser pytest -m browser \
  --tracing retain-on-failure \
  --screenshot only-on-failure \
  --full-page-screenshot
```

Expected: all browser tests pass at all three viewports, no external request is observed,
and no real queue or token state is read or mutated.

- [ ] **Step 7: Commit CI and documentation**

```bash
git add .github/workflows/browser-ui.yml README.md tests/unit/test_project_contract.py
git commit -m "ci: run isolated browser UI tests"
```

- [ ] **Step 8: Review and deployment handoff**

Request a final review of all commits against the design. Fix every Critical or Important
finding with a failing test first. Re-run both the normal and standalone browser gates.
This test-only feature does not require a Supervisor restart; push only after explicit
authorization or an already-approved integration instruction.
