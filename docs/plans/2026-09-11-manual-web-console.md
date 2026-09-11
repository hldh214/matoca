# Manual Japanese Web Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a dense, responsive Japanese console for selecting a merchant, comparing current shop queues, managing the active queue, and joining with minimal input.

**Architecture:** Add a pure presentation layer that converts SQLite-backed snapshots into explicit Japanese shop states, plus narrow preference APIs backed by the existing singleton repository. Replace the monolithic browser script with focused ES modules while preserving the current live waiting and same-origin mutation boundaries.

**Tech Stack:** CPython 3.14, FastAPI, Pydantic v2, Jinja2, dependency-free ES modules, SQLite, pytest, Node.js synthetic DOM checks

**Spec:** `docs/designs/2026-09-11-manual-web-console-design.md`

## Global Constraints

- Keep every visible label, error, loading state, dialog, and accessibility name Japanese.
- Use `加盟店`, not `ブランド`, in visible copy.
- Keep the universal merchant layout; do not add Sawayaka-specific region grouping.
- Put global settings in the application header; do not use a floating panel.
- Default the shop filter to currently issuable shops.
- Label upstream waiting time `公式目安`; do not present it as a local prediction.
- Prefill two adults and zero children by default, clamped to the live shop form.
- Automatically select Sawayaka's sole enabled confirmation item.
- Do not show prediction, automation, arrival-time, or Web Push controls in this phase.
- Cached console reads must not contact Matoca or multiply background collection.
- Keep queue state independent from catalog state and preserve same-origin mutation checks.
- Never submit or cancel a real queue during implementation or verification.
- Use `uv`; do not use system Python or `pip`.

---

### Task 1: Cached Console View Model

**Files:**
- Create: `src/matoca_service/console.py`
- Modify: `src/matoca_service/service.py`
- Modify: `src/matoca_service/web/app.py`
- Create: `tests/unit/test_console.py`
- Modify: `tests/unit/web/test_app.py`

**Interfaces:**
- Produces: `ShopStatus = Literal["available", "closed", "holiday", "suspended", "stale"]`.
- Produces: `ShopConsoleItem`, `MerchantConsoleData`, and `build_console(merchant: MerchantSummary, stored_shops: list[StoredShop], catalog: CatalogState | None, poll_state: MerchantPollState, now: datetime) -> MerchantConsoleData`.
- Produces: `MatocaService.merchant_console(merchant_key: str) -> MerchantConsoleData` using SQLite only.
- Adds: `GET /api/merchants/{merchant_key}/console`.
- Consumes: existing `ShopRepository.snapshot()`, `StoredShop`, merchant registry, and timezone localization.

- [ ] **Step 1: Write failing status-mapping tests**

Create table-driven tests in `tests/unit/test_console.py` with fresh shops for these exact mappings:

```python
@pytest.mark.parametrize(
    ("shop", "detail_fresh", "status", "label", "can_join"),
    [
        (Shop(id=1, name="A", is_open=True, is_issuable=True), True,
         "available", "受付可能", True),
        (Shop(id=1, name="A", is_open=False, is_issuable=False), True,
         "closed", "営業時間外", False),
        (Shop(id=1, name="A", is_holiday=True), True,
         "holiday", "休業", False),
        (Shop(id=1, name="A", is_open=True, is_suspended=True), True,
         "suspended", "受付停止", False),
        (Shop(id=1, name="A", is_open=True, is_issuable=True), False,
         "stale", "更新待ち", False),
    ],
)
def test_console_resolves_shop_status(shop, detail_fresh, status, label, can_join):
    item = console_item(shop, detail_fresh=detail_fresh)
    assert (item.status, item.status_label, item.can_join) == (status, label, can_join)
```

Also assert `official_waiting_minutes`, `official_waiting_is_more`, `forms`, and the
shop observation timestamp are preserved without prediction fields.

- [ ] **Step 2: Run the status tests and confirm RED**

Run: `uv run pytest tests/unit/test_console.py -v`

Expected: FAIL because `matoca_service.console` does not exist.

- [ ] **Step 3: Implement the presentation models and pure mapper**

Use Pydantic models with `extra="forbid"`:

```python
class ShopConsoleItem(BaseModel):
    id: int
    name: str
    sub_name: str | None
    address: str | None
    image_url: str | None
    current_waiting: int
    official_waiting_minutes: int | None
    official_waiting_is_more: bool
    status: ShopStatus
    status_label: str
    can_join: bool
    stale: bool
    updated_at: datetime | None
    forms: ShopForms | None

class MerchantConsoleData(BaseModel):
    merchant: MerchantSummary
    updated_at: datetime
    stale: bool
    available_count: int
    total_count: int
    shops: list[ShopConsoleItem]
```

Status precedence is stale, holiday, closed, suspended/non-issuable, available. A shop
without a current observation is stale. The mapper must not read state files or clients.

- [ ] **Step 4: Add the SQLite-only service method and route tests**

Test `MatocaService.merchant_console()` by monkeypatching `_authenticated_read` and
`read_collection_cycle` to raise if called. Assert the stored shop is returned. Extend the
Web fake service and assert:

```python
response = await client.get(
    "/api/merchants/sawayaka/console",
    headers={"X-Timezone": "America/New_York"},
)
assert response.status_code == 200
assert response.json()["shops"][0]["status_label"] == "受付可能"
assert response.json()["updated_at"].endswith("-04:00")
```

- [ ] **Step 5: Implement `merchant_console` and the cached route**

Read `ShopRepository.snapshot()` through `run_storage`. The route localizes console and
shop timestamps using the existing timezone helpers. It must not call `merchant_snapshot`,
because that method may wake collection on a cold cache.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/unit/test_console.py tests/unit/web/test_app.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/console.py src/matoca_service/service.py \
  src/matoca_service/web/app.py tests/unit/test_console.py tests/unit/web/test_app.py
git commit -m "feat: expose cached Japanese merchant console"
```

### Task 2: Global Party Preferences API

**Files:**
- Modify: `src/matoca_service/service.py`
- Modify: `src/matoca_service/web/app.py`
- Modify: `tests/unit/test_service.py`
- Modify: `tests/unit/web/test_app.py`

**Interfaces:**
- Produces: `PartyPreferences(default_adult_count: int, default_child_count: int)` with both values constrained to `0..20`.
- Produces: `MatocaService.party_preferences() -> Awaitable[PartyPreferences]`.
- Produces: `MatocaService.update_party_preferences(PartyPreferences) -> Awaitable[PartyPreferences]`.
- Adds: `GET /api/preferences` and same-origin protected `PUT /api/preferences`.
- Consumes: existing `PreferenceRepository` and `UserPreferences` while preserving both prediction tolerance columns unchanged.

- [ ] **Step 1: Write failing service tests**

```python
preferences = await service.party_preferences()
assert preferences.model_dump() == {
    "default_adult_count": 2,
    "default_child_count": 0,
}

updated = await service.update_party_preferences(PartyPreferences(
    default_adult_count=3,
    default_child_count=1,
))
stored = PreferenceRepository(service._database).get()
assert (stored.default_adult_count, stored.default_child_count) == (3, 1)
assert (stored.early_tolerance_minutes, stored.model_error_minutes) == (15, 15)
```

- [ ] **Step 2: Write failing API validation and origin tests**

Assert GET returns `2/0`, PUT with the correct Origin returns `3/1`, PUT without Origin is
403, and a value of 21 returns 422. Use a fake service; do not touch runtime data.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `uv run pytest tests/unit/test_service.py tests/unit/web/test_app.py -q -k preferences`

Expected: FAIL because the models and methods do not exist.

- [ ] **Step 4: Implement service and API methods**

Instantiate `PreferenceRepository` alongside `ShopRepository`. Read and write with
`run_storage`. When updating, load the full `UserPreferences`, replace only the two party
counts, and retain both tolerance values. Protect PUT with `require_same_origin`.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/unit/test_service.py tests/unit/web/test_app.py -q -k preferences`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/matoca_service/service.py src/matoca_service/web/app.py \
  tests/unit/test_service.py tests/unit/web/test_app.py
git commit -m "feat: manage queue party defaults"
```

### Task 3: Merchant Selector And Console Shell

**Files:**
- Modify: `src/matoca_service/web/templates/dashboard.html`
- Modify: `src/matoca_service/web/templates/merchant.html`
- Modify: `src/matoca_service/web/static/dashboard.css`
- Modify: `src/matoca_service/web/static/merchant-selector.css`
- Modify: `src/matoca_service/web/static/merchant.css`
- Modify: `tests/unit/web/test_app.py`

**Interfaces:**
- Produces stable DOM IDs: `settings-button`, `refresh-button`, `current-queue`, `shop-filter`, `available-count`, `total-count`, `shop-search`, `shop-list`, `updated-at`, `join-dialog`, `settings-dialog`, and `cancel-dialog`.
- Consumes full merchant names and image URLs from the existing Jinja context.

- [ ] **Step 1: Write failing template contract tests**

Assert the home page contains `利用する加盟店を選ぶ`, both full merchant names, and no
`ブランド`. Assert the merchant page contains every stable ID, `受付可能`, `すべて`,
`現在の順番待ち`, `公式目安`, `設定`, and exactly three close buttons. Assert it contains
neither `地域別` nor any automatic/prediction copy.

- [ ] **Step 2: Run template tests and confirm RED**

Run: `uv run pytest tests/unit/web/test_app.py -q -k 'home_page or merchant_page'`

Expected: FAIL on the new copy, settings dialog, and DOM IDs.

- [ ] **Step 3: Replace the merchant-selector shell**

Use a 60 px header and a constrained content area. Each merchant link uses its registry
image as the primary visual, its full name as the heading, and `店舗を見る` as the action.
Cards have an 8 px maximum radius and remain one column on mobile.

- [ ] **Step 4: Replace the merchant console shell**

Build a compact header with back icon, merchant name, settings icon button, and refresh
icon button. Follow it with the fixed-height queue band, toolbar, count summary, stable
desktop column header, unframed shop list, and updated timestamp. Use semantic buttons,
forms, labels, outputs, and Japanese aria labels.

The settings dialog contains only adult and child steppers plus `設定を保存`. The join
dialog contains selected shop metrics, dynamic adult/child rows, `confirm-items`, an error
region, and `この内容で順番待ちを申し込む`. The cancel dialog remains explicit and red.

- [ ] **Step 5: Implement responsive CSS**

Use neutral white/gray surfaces, charcoal text, green availability, and red primary queue
actions. Avoid gradients, decorative orbs, oversized headings, nested cards, negative
letter spacing, and viewport-scaled font sizes. Desktop rows use stable tracks for shop,
status, groups, official estimate, and action. Under 760 px, hide headings and use two
columns; identity and primary action span both columns. All buttons have stable heights.

- [ ] **Step 6: Run template tests**

Run: `uv run pytest tests/unit/web/test_app.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/web/templates src/matoca_service/web/static/dashboard.css \
  src/matoca_service/web/static/merchant-selector.css \
  src/matoca_service/web/static/merchant.css tests/unit/web/test_app.py
git commit -m "feat: build Japanese queue console shell"
```

### Task 4: Modular Shop, Queue, Join, And Settings Interactions

**Files:**
- Create: `src/matoca_service/web/static/api.js`
- Create: `src/matoca_service/web/static/shop-list.js`
- Create: `src/matoca_service/web/static/queue-status.js`
- Create: `src/matoca_service/web/static/join-form.js`
- Create: `src/matoca_service/web/static/preferences.js`
- Modify: `src/matoca_service/web/static/merchant.js`
- Modify: `src/matoca_service/web/templates/merchant.html`
- Create: `tests/unit/web/test_static_assets.py`
- Replace: `tests/unit/web/merchant_state.cjs`
- Modify: `tests/unit/web/test_merchant_state.py`

**Interfaces:**
- Produces: `MatocaApi.console()`, `currentWaiting()`, `shopDetail(id)`, `createWaiting(body)`, `cancelWaiting(id)`, `preferences()`, `savePreferences(body)`, and `refresh()`.
- Produces: `ShopList.render(data, filter, query, queueKnown, hasQueue)` and click callback.
- Produces: `QueueStatus` with response-revision protection.
- Produces: `JoinForm.open(shop)`, `submit()`, and live shop-form handling.
- Produces: `PreferencesDialog.open()` and `save()`.
- Consumes: `/console`, `/waiting`, `/api/shops/{id}`, `/refresh`, and `/api/preferences`.

- [ ] **Step 1: Write failing static asset contract tests**

Assert all five modules exist; `merchant.js` imports them, contains `/console` indirectly
through `api.js`, and does not contain HTML interpolation or the old `/snapshot` path.
Assert no English loading/error strings appear in JS files.

- [ ] **Step 2: Write failing behavioral harness cases**

Replace the synthetic DOM harness to load the ES modules and verify:

1. Initial filter is available and count text reflects available/total.
2. An unknown queue state disables every join action.
3. Catalog refresh cannot erase an active queue.
4. A stale waiting response cannot resurrect a cancelled queue.
5. Settings load 2/0, save 3/1, and affect the next dialog only.
6. Live shop detail clamps counts and hides unsupported child input.
7. One enabled confirmation defaults to its enabled choice.
8. Unsupported multiple confirmations disable submission with
   `選択内容の確認が必要です`.

- [ ] **Step 3: Run tests and confirm RED**

Run: `uv run pytest tests/unit/web/test_static_assets.py tests/unit/web/test_merchant_state.py -v`

Expected: FAIL because the modules do not exist and the old monolith remains.

- [ ] **Step 4: Implement the API module**

All requests add `X-Timezone` using the browser zone or `Asia/Tokyo`. Parse JSON errors
into one `MatocaApiError` carrying the Japanese `detail`. Never log bodies. Mutations use
same-origin fetch and rely on the browser-supplied Origin.

- [ ] **Step 5: Implement safe shop-list and queue rendering**

Build upstream content with `createElement` and `textContent`; never concatenate shop names,
addresses, or image URLs into `innerHTML`. Render explicit server status labels and use
`can_join` directly. Preserve queue revision ownership from the current implementation.

- [ ] **Step 6: Implement live join and preference dialogs**

On open, fetch current detail for the selected shop. Initialize counts from saved global
preferences, then clamp to `min_*` and `max_*`. Hide child input when the live maximum is
zero. Auto-select exactly one enabled confirmation choice. Multiple enabled choices require
explicit user confirmation; if the response shape cannot be represented safely, disable
submit and show the specified Japanese message.

- [ ] **Step 7: Reduce `merchant.js` to orchestration**

Load console and current waiting independently, wire filter/search/header commands, refresh
console every 30 seconds while visible, refresh both on visibility return, and never call
the force-refresh endpoint automatically. Manual refresh calls `api.refresh()` once and
then reloads cached console data.

- [ ] **Step 8: Parse modules and run focused tests**

Run each command:

```bash
node --check src/matoca_service/web/static/api.js
node --check src/matoca_service/web/static/shop-list.js
node --check src/matoca_service/web/static/queue-status.js
node --check src/matoca_service/web/static/join-form.js
node --check src/matoca_service/web/static/preferences.js
node --check src/matoca_service/web/static/merchant.js
uv run pytest tests/unit/web -v
```

Expected: all commands exit 0.

- [ ] **Step 9: Commit**

```bash
git add src/matoca_service/web/static src/matoca_service/web/templates/merchant.html \
  tests/unit/web
git commit -m "feat: add focused queue console interactions"
```

### Task 5: Compatibility, Responsive Verification, And Deployment Handoff

**Files:**
- Modify: `src/matoca_service/web/app.py`
- Modify: `tests/unit/web/test_app.py`
- Modify: `tests/unit/test_project_contract.py`
- Modify: `README.md`

**Interfaces:**
- Removes: obsolete `GET /api/merchants/{merchant_key}/snapshot` after browser code no longer references it.
- Preserves: `/api/dashboard`, shop detail, waiting detail, current waiting, create waiting, cancel waiting, and manual refresh routes.
- Documents: manual console behavior, cached/live boundaries, and global party defaults.

- [ ] **Step 1: Add compatibility and package tests**

Assert static assets and both templates are present in the wheel/sdist contract. Assert the
old snapshot route returns 404, preserved routes retain their response models, and all
state-changing APIs still reject a missing or foreign Origin.

- [ ] **Step 2: Remove the obsolete snapshot route and update README**

Remove the route only after Task 4 uses `/console`. Document the two supported merchants,
default available filter, official estimate label, header settings, 2/0 defaults, and that
active queue state is live while shop state is cached.

- [ ] **Step 3: Run focused Web and packaging tests**

Run:

```bash
uv run pytest tests/unit/web tests/unit/test_project_contract.py -v
```

Expected: PASS.

- [ ] **Step 4: Run the full quality gate**

Run exactly:

```bash
uv run pytest
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv build
git diff --check
```

Expected: all exit 0 with no warnings or failures.

- [ ] **Step 5: Commit the completed console**

```bash
git add src/matoca_service/web/app.py tests/unit/web/test_app.py \
  tests/unit/test_project_contract.py README.md
git commit -m "docs: finish manual queue console"
```

- [ ] **Step 6: Verify deployment without queue mutations**

Restart only through `supervisorctl restart matoca`. Confirm `supervisorctl status matoca`
is RUNNING and the local home page returns HTTP 200. Provide
`http://192.168.10.103:48173/` to the user for visual verification at 1440x900 and 390x844.
Do not submit or cancel any queue while checking the live UI.
