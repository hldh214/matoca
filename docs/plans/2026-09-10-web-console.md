# Japanese Queue Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved Japanese merchant console for instant shop comparison, one-screen automated-task setup, and persistent current queue status.

**Architecture:** Add a presentation service that combines cached observations, predictions, preferences, tasks, and queue sessions into one stable JSON view. Keep server-rendered Japanese shells and dependency-free ES modules; split API, rendering, queue forms, and automation forms so the existing monolithic script does not grow further.

**Tech Stack:** FastAPI, Jinja2, HTML, CSS, browser ES modules, Pydantic v2, pytest, Playwright for visual verification

**Spec:** `docs/designs/2026-09-10-queue-automation-design.md`

## Global Constraints

- Complete the storage/collection and prediction/automation plans first.
- Keep every visible label, status, empty state, validation message, and notification in Japanese.
- Keep the universal merchant layout; do not require merchant-specific region grouping.
- Put the automation action and next-task summary in the merchant page header; do not use a floating side panel.
- Default the shop filter to currently issuable shops.
- Prefill two adults and zero children, subject to live form visibility and min/max.
- Automatically select Sawayaka's sole enabled missed-call confirmation.
- Read cached data on page load; browser traffic must not multiply upstream Matoca polling.
- Keep one-process, one-worker behavior and the existing same-origin protection.
- Never submit or cancel a real queue during browser verification.

---

### Task 1: Stable Merchant Console View Model

**Files:**
- Create: `src/matoca_service/web/models.py`
- Create: `src/matoca_service/web/console.py`
- Modify: `src/matoca_service/web/app.py`
- Test: `tests/unit/web/test_console.py`
- Modify: `tests/unit/web/test_app.py`

**Interfaces:**
- Produces: `ShopConsoleItem`, `TaskConsoleItem`, `QueueConsoleItem`, and `MerchantConsoleData` Pydantic models.
- Produces: `ConsoleService.get(merchant_key: str, timezone: ZoneInfo) -> MerchantConsoleData`.
- Adds: `GET /api/merchants/{merchant_key}/console`.
- Consumes: cached shop repository, prediction service, preferences, automation repository, and queue-session repository.

- [ ] **Step 1: Write a failing aggregation test**

```python
def test_console_combines_cached_shop_prediction_and_active_task() -> None:
    console = ConsoleService(repositories, predictor, now=fixed_now).get("sawayaka", TOKYO)

    assert console.merchant.key == "sawayaka"
    assert console.shops[0].current_waiting == 42
    assert console.shops[0].prediction.fast_minutes == 51
    assert console.shops[0].prediction.typical_minutes == 68
    assert console.active_task.shop_id == 3272
    assert console.updated_at.isoformat() == "2026-09-10T17:24:00+09:00"
```

- [ ] **Step 2: Write a failing stale-data test**

```python
def test_console_marks_partial_detail_stale_and_not_issuable() -> None:
    console = console_from(detail_fresh=False, waiting_minutes=None)

    assert console.shops[0].stale is True
    assert console.shops[0].can_join is False
    assert console.shops[0].stale_message == "店舗詳細を更新できませんでした"
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `uv run pytest tests/unit/web/test_console.py tests/unit/web/test_app.py -v`

Expected: FAIL because the console models and route do not exist.

- [ ] **Step 4: Implement view models with Japanese status codes resolved server-side**

```python
class ShopConsoleItem(BaseModel):
    id: int
    name: str
    address: str | None
    image_url: str | None
    status: Literal["available", "closed", "holiday", "suspended", "stale"]
    status_label: str
    current_waiting: int
    matoca_waiting_minutes: int | None
    prediction: PredictionResult | None
    can_join: bool
    stale: bool
    updated_at: datetime
```

Map statuses to `受付可能`, `営業時間外`, `休業`, `受付停止`, and `更新待ち`. Do not let the browser infer safety from incomplete booleans. Convert all response timestamps with the existing `parse_timezone`/`localize_datetime` path.

- [ ] **Step 5: Add the cached console route**

Read only SQLite-backed services. The route must not call Matoca. Preserve `/snapshot` temporarily for compatibility until the new JavaScript lands, then remove it in Task 3 after tests stop using it.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/unit/web/test_console.py tests/unit/web/test_app.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/web tests/unit/web
git commit -m "feat: expose cached merchant console data"
```

### Task 2: Approved Merchant Header and Universal Shop List

**Files:**
- Modify: `src/matoca_service/web/templates/merchant.html`
- Modify: `src/matoca_service/web/templates/dashboard.html`
- Modify: `src/matoca_service/web/static/merchant.css`
- Modify: `src/matoca_service/web/static/merchant-selector.css`
- Modify: `tests/unit/web/test_app.py`

**Interfaces:**
- Produces: stable DOM IDs `automation-button`, `next-task-summary`, `current-status`, `shop-filter`, `shop-search`, `shop-list`, and `updated-at`.
- Consumes: Japanese merchant name and supported merchant list from existing Jinja context.

- [ ] **Step 1: Write failing template assertions**

```python
@pytest.mark.asyncio
async def test_merchant_page_has_approved_japanese_console_structure() -> None:
    response = await client.get("/merchants/sawayaka")

    assert 'id="automation-button"' in response.text
    assert 'id="next-task-summary"' in response.text
    assert 'id="current-status"' in response.text
    assert "自動受付を設定" in response.text
    assert "受付可能" in response.text
    assert "すべて" in response.text
    assert "地域別" not in response.text
```

- [ ] **Step 2: Run the template test and confirm failure**

Run: `uv run pytest tests/unit/web/test_app.py::test_merchant_page_has_approved_japanese_console_structure -v`

Expected: FAIL because the approved header IDs are absent.

- [ ] **Step 3: Replace the merchant page shell**

Use a compact application header, merchant title and header actions, persistent status band, segmented availability filter, search, dense list headings, updated timestamp, manual queue dialog, automation dialog, task edit/cancel dialog, and queue cancel dialog. Use semantic `button`, `dialog`, `form`, `label`, and `output` elements and Japanese `aria-label` values.

The title remains merchant-first in the initial viewport. Do not create a hero, nested cards, region sidebar, or explanatory marketing copy.

- [ ] **Step 4: Add responsive structural CSS**

Desktop shop rows use stable grid tracks for store, status, groups, prediction, and action. Below 760px hide the table heading and stack each shop into two columns with the identity and action spanning the full width. The automation header actions wrap beneath the merchant name. Use radii no larger than 8px, zero letter spacing, and no viewport-scaled font sizes.

- [ ] **Step 5: Keep merchant selector capability-focused**

Preserve the full labels `炭焼きレストラン さわやか` and `ラ・オハナ 横浜本牧`. Add compact status areas for an active task or current queue without changing merchant routing or introducing region terminology.

- [ ] **Step 6: Run template tests**

Run: `uv run pytest tests/unit/web/test_app.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/web/templates src/matoca_service/web/static/merchant.css src/matoca_service/web/static/merchant-selector.css tests/unit/web/test_app.py
git commit -m "feat: build universal Japanese shop console"
```

### Task 3: Split Browser Data and Rendering Modules

**Files:**
- Create: `src/matoca_service/web/static/api.js`
- Create: `src/matoca_service/web/static/shop-list.js`
- Create: `src/matoca_service/web/static/status-band.js`
- Modify: `src/matoca_service/web/static/merchant.js`
- Modify: `src/matoca_service/web/templates/merchant.html`
- Test: `tests/unit/web/test_static_assets.py`

**Interfaces:**
- Produces: `MatocaApi`, `renderShopList(consoleData, filter, query)`, and `renderStatusBand(consoleData)` ES module exports.
- Consumes: `GET /api/merchants/{merchant_key}/console` and existing same-origin headers.

- [ ] **Step 1: Write failing static-module contract tests**

```python
def test_merchant_script_uses_cached_console_endpoint() -> None:
    source = (WEB_STATIC / "merchant.js").read_text()
    assert "/console" in source
    assert "/snapshot" not in source
    assert "setInterval" in source


def test_visible_static_copy_is_japanese() -> None:
    combined = "\n".join(path.read_text() for path in WEB_STATIC.glob("*.js"))
    assert "Unable to" not in combined
    assert "Loading" not in combined
```

- [ ] **Step 2: Run the static tests and confirm failure**

Run: `uv run pytest tests/unit/web/test_static_assets.py -v`

Expected: FAIL because the split modules do not exist and the old endpoint remains.

- [ ] **Step 3: Implement one local API client**

```javascript
export class MatocaApi {
  constructor(merchantKey) {
    this.merchantKey = merchantKey;
    this.headers = {"X-Timezone": Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Tokyo"};
  }

  async console() {
    return this.request(`/api/merchants/${this.merchantKey}/console`);
  }
}
```

`request()` must parse structured Japanese errors, throw one typed browser error, and add `Origin` implicitly through same-origin fetch. Never log response bodies or subscription data.

- [ ] **Step 4: Implement safe rendering and refresh behavior**

Build all dynamic text through DOM nodes and `textContent`; do not interpolate upstream strings into HTML. Default to `available`, retain text search, display `通常` and `早い場合`, and disable action buttons when `can_join` is false or an active queue exists.

Refresh the local cached console every 30 seconds while visible and immediately on `visibilitychange`. The browser never calls an upstream-forcing refresh automatically. A manual refresh button requests only a collector wake-up and then reloads cached data.

- [ ] **Step 5: Remove the obsolete snapshot route and monolithic rendering code**

Delete `/api/merchants/{merchant_key}/snapshot` only after all Web code and tests use `/console`. Retain old `/api/dashboard` and detail routes for CLI/backward compatibility unless an existing test proves they are unused public surface.

- [ ] **Step 6: Parse every JavaScript module and run tests**

Run: `node --check src/matoca_service/web/static/api.js`

Expected: no output, exit 0.

Run: `node --check src/matoca_service/web/static/shop-list.js`

Expected: no output, exit 0.

Run: `node --check src/matoca_service/web/static/status-band.js`

Expected: no output, exit 0.

Run: `node --check src/matoca_service/web/static/merchant.js`

Expected: no output, exit 0.

Run: `uv run pytest tests/unit/web -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/web tests/unit/web
git commit -m "refactor: split merchant console modules"
```

### Task 4: One-Screen Automation Dialog and Preferences

**Files:**
- Create: `src/matoca_service/web/static/automation-form.js`
- Create: `src/matoca_service/web/static/preferences.js`
- Modify: `src/matoca_service/web/static/api.js`
- Modify: `src/matoca_service/web/static/merchant.js`
- Modify: `src/matoca_service/web/templates/merchant.html`
- Modify: `src/matoca_service/web/static/merchant.css`
- Modify: `tests/unit/web/test_app.py`
- Modify: `tests/unit/web/test_static_assets.py`

**Interfaces:**
- Produces: `AutomationForm.open(prefilledShopId?: number)`, `submit()`, `edit(taskId)`, and `cancel(taskId)`.
- Produces: preferences dialog for adult/child and 15+15 defaults.
- Consumes: preferences and automation task APIs from the prediction/automation plan.

- [ ] **Step 1: Write failing form-shell and copy tests**

```python
@pytest.mark.asyncio
async def test_automation_dialog_is_single_screen_and_japanese() -> None:
    response = await client.get("/merchants/sawayaka")

    assert 'id="automation-dialog"' in response.text
    assert "到着予定を設定" in response.text
    assert "早めの呼出許容" in response.text
    assert "予測誤差の余裕" in response.text
    assert "この条件で自動受付を有効にする" in response.text
    assert "次へ" not in response.text
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/web/test_app.py tests/unit/web/test_static_assets.py -v`

Expected: FAIL because the automation dialog and module are absent.

- [ ] **Step 3: Implement single-screen task setup**

Load preferences and available shops when opening. Header invocation requires selecting a shop; invoking from a shop action preselects it. Require local date/time and party counts. Keep risk settings in an openable `詳細設定` section with 15-minute defaults. Show current groups, typical prediction, fast-case prediction, confidence label, and this authorization text:

```text
条件が成立すると、再確認せずに順番待ちを申し込みます。
```

- [ ] **Step 4: Apply dynamic merchant form behavior**

Fetch fresh shop detail when the selected shop changes. Hide child input when disabled. Auto-select and display Sawayaka's sole enabled confirmation as accepted. La Ohana shows adult and child inputs and no confirmation. When the server reports an unsupported multi-choice form, display `選択内容の確認が必要です` and disable activation.

- [ ] **Step 5: Implement preferences without extra configuration files**

The settings dialog edits SQLite preferences. Every new manual or automated form uses those values, clamps them to live limits, and leaves the current form editable. Do not persist per-use arrival times as defaults.

- [ ] **Step 6: Parse scripts and run Web tests**

Run: `node --check src/matoca_service/web/static/automation-form.js`

Expected: no output, exit 0.

Run: `node --check src/matoca_service/web/static/preferences.js`

Expected: no output, exit 0.

Run: `uv run pytest tests/unit/web -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/web tests/unit/web
git commit -m "feat: add one-screen automatic queue setup"
```

### Task 5: Manual Queue Form and Persistent Status Band

**Files:**
- Create: `src/matoca_service/web/static/manual-queue.js`
- Modify: `src/matoca_service/web/static/status-band.js`
- Modify: `src/matoca_service/web/static/shop-list.js`
- Modify: `src/matoca_service/web/static/merchant.js`
- Modify: `src/matoca_service/web/static/merchant.css`
- Modify: `tests/unit/web/test_static_assets.py`
- Test: `tests/unit/web/test_console_states.py`

**Interfaces:**
- Produces: manual join/cancel controllers and rendered monitoring, queued, attention, failure, and empty states.
- Consumes: current queue APIs, automation APIs, and `MerchantConsoleData`.

- [ ] **Step 1: Write failing presentation-state tests**

```python
@pytest.mark.parametrize(
    ("state", "label"),
    [("monitoring", "自動受付を監視中"), ("queued", "順番待ち受付済み"),
     ("needs_attention", "確認が必要です"), ("expired", "受付できませんでした")],
)
def test_console_state_has_japanese_label(state: str, label: str) -> None:
    assert task_console(state).status_label == label
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `uv run pytest tests/unit/web/test_console_states.py tests/unit/web/test_static_assets.py -v`

Expected: FAIL until all states and scripts exist.

- [ ] **Step 3: Implement manual queue actions from resolved live forms**

Use the same server form resolution as automated tasks. Manual join displays only enabled inputs and defaults all unambiguous confirmations. Its submit button remains an explicit immediate action. Keep the existing cancellation confirmation dialog and same-origin calls.

- [ ] **Step 4: Render task and queue status in the approved band**

Monitoring shows target shop, arrival, current waiting, fast prediction, current decision, and next evaluation. Queued shows shop, queue number, groups ahead, predicted call time, arrival, progress, and cancellation. Attention/failure states show the stable Japanese reason and a direct edit/dismiss action.

On the merchant selector, add a compact task/current-queue indicator to the matching merchant without turning the selector into an operations dashboard.

- [ ] **Step 5: Disable conflicting writes**

When any active queue exists, disable every immediate join button and show `順番待ち受付中`. Prevent a second browser click while a request is pending. A task cancellation changes only the planned task; queue cancellation is a separate explicit action.

- [ ] **Step 6: Run static and Web tests**

Run: `node --check src/matoca_service/web/static/manual-queue.js`

Expected: no output, exit 0.

Run: `uv run pytest tests/unit/web -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/web tests/unit/web
git commit -m "feat: show live automation and queue status"
```

### Task 6: Responsive and Visual Verification

**Files:**
- Modify: `src/matoca_service/web/static/dashboard.css`
- Modify: `src/matoca_service/web/static/merchant.css`
- Modify: `README.md`
- Test: `tests/unit/web/test_app.py`

**Interfaces:**
- Consumes: completed console HTML and scripts.
- Produces: verified desktop and mobile layouts with no new runtime interface.

- [ ] **Step 1: Add structural regression assertions**

Assert the response contains one top-level application header, one merchant header, one status band, one shop list, and dialogs that are siblings rather than nested cards. Assert every input has a Japanese label and every icon-only button has a Japanese accessible name.

- [ ] **Step 2: Start the development service through Supervisor**

Run: `supervisorctl restart matoca`

Expected: process enters `RUNNING`; do not start the removed systemd unit.

- [ ] **Step 3: Verify desktop at 1440x900 using mocked write requests**

Open the configured Cloudflare or LAN URL in Playwright, intercept every `POST /api/merchants/*/waiting` and every waiting `DELETE`, and capture a screenshot. Verify the merchant name, header automation action, current status, filter/search, at least three shop rows, and updated time are visible. Verify `document.documentElement.scrollWidth === document.documentElement.clientWidth`.

- [ ] **Step 4: Verify mobile at 390x844 using mocked write requests**

Capture the merchant list, automation dialog, monitoring band, and queued band. Verify buttons remain at least 42px high, no label overlaps a value, dialogs fit the viewport, the longest Japanese label wraps, and horizontal overflow is absent.

- [ ] **Step 5: Check interaction states**

Exercise availability filtering, search, opening/closing both forms, count steppers at min/max, timezone conversion, task create/edit/cancel with API writes intercepted, current queue display, and stale snapshot copy. Do not allow an intercepted browser session to reach a real queue POST/DELETE.

- [ ] **Step 6: Update README and run the quality gate**

Document the merchant selector, cached minute view, automatic task setup, party defaults, Japanese-only UI, and responsive behavior.

Run: `uv run pytest`

Expected: PASS.

Run: `uv run ruff check src tests`

Expected: PASS.

Run: `uv run ruff format --check src tests`

Expected: PASS.

Run: `uv run mypy src`

Expected: PASS.

Run: `uv build`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add README.md src/matoca_service/web tests/unit/web
git commit -m "feat: polish responsive queue console"
```
