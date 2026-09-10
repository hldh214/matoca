# Matoca Read Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add capture-backed shop details, non-empty waiting parsing, and read-only waiting-detail access without performing any real queue mutation.

**Architecture:** Extend the existing permissive Pydantic Matoca models with typed fields proven by captures, while retaining unknown response fields through `extra="allow"`. Add focused methods to `MatocaClient`, then expose authenticated detail reads through the existing single-process `MatocaService` lock and FastAPI application. Reuse the existing one-time LIFF reissue behavior for 401/403 responses rather than creating a second authentication path.

**Tech Stack:** uv-managed CPython 3.14, Pydantic v2, HTTPX, FastAPI, pytest, pytest-asyncio, respx, Ruff, and MyPy.

**Spec:** `README.md` and the private capture evidence listed by `/mnt/ssd-4t/data/matoca-codex-handoff/MANIFEST.md`

## Global Constraints

- Run every Python command through `uv`; do not use the system Python.
- Never print, log, fixture, or commit a real LINE Native, LIFF, or ID token.
- Never copy captures, `state.json`, device identifiers, or account identifiers into the repository.
- Treat capture-observed fields as facts, behavioral conclusions as inference, and undocumented response fields as unknown.
- Use Pydantic/JSON parsing; do not search payload text for credentials or protocol fields.
- Keep mutable credentials exclusively in ignored `state.json`, with mode `0600` preserved by the existing store.
- Keep the runtime single-process and single-worker; retain the existing `asyncio.Lock` boundary.
- Do not call `POST /liff/waiting`, `DELETE /liff/waiting/{waiting_id}`, or force Native Refresh during implementation or verification.
- Keep the Supervisor-managed service available at `192.168.10.103:48173`; restart it only after offline checks pass.

---

### Task 1: Capture-Backed Shop Detail Models

**Files:**
- Modify: `src/matoca_service/matoca/models.py:1-27`
- Create: `tests/unit/matoca/test_shop_models.py`

**Interfaces:**
- Consumes: JSON object from Matoca `GET /liff/shops/{shop_id}` at `content.shop`.
- Produces: `WaitingEstimate`, `WaitingOptions`, `ShopForms`, and extended `Shop` models. All inherit the existing `MatocaModel` behavior so additive private-API fields remain accepted.

- [ ] **Step 1: Write a failing model test for the captured shop-detail fields**

```python
from matoca_service.matoca.models import Shop


def test_shop_detail_parses_ticketing_rules_and_wait_estimate() -> None:
    shop = Shop.model_validate(
        {
            "id": 3272,
            "name": "synthetic merchant",
            "forms": {
                "is_ticketing_only": False,
                "is_confirm_tel": False,
                "min_adult": 1,
                "max_adult": 10,
                "default_value_adult": 2,
                "min_child": 0,
                "max_child": 10,
                "default_value_child": 0,
                "is_confirm_child": True,
            },
            "options": {
                "waiting": {
                    "enabled": True,
                    "show_waiting": True,
                    "unit": "groups",
                    "is_display_waiting_group": True,
                    "is_display_waiting_time": True,
                }
            },
            "waiting_time": {"minutes": 90, "is_more": True},
            "is_issuable": True,
            "is_open": True,
            "is_issuable_area": True,
            "unknown_future_field": "preserved",
        }
    )

    assert shop.forms is not None
    assert shop.forms.default_value_adult == 2
    assert shop.options.waiting is not None
    assert shop.options.waiting.is_display_waiting_time is True
    assert shop.waiting_time is not None
    assert shop.waiting_time.minutes == 90
    assert shop.model_extra == {"unknown_future_field": "preserved"}
```

- [ ] **Step 2: Run the model test and verify RED**

Run: `uv run pytest tests/unit/matoca/test_shop_models.py -v`

Expected: FAIL because `Shop.options` is an untyped dictionary and the detail model fields do not exist.

- [ ] **Step 3: Implement the minimal typed detail models**

```python
class WaitingOptions(MatocaModel):
    enabled: bool = False
    show_waiting: bool = False
    unit: str | None = None
    is_display_waiting_group: bool = False
    is_display_waiting_time: bool = False


class ShopOptions(MatocaModel):
    waiting: WaitingOptions | None = None


class ShopForms(MatocaModel):
    is_ticketing_only: bool = False
    is_confirm_tel: bool = False
    min_adult: int = 0
    max_adult: int = 0
    default_value_adult: int = 0
    min_child: int = 0
    max_child: int = 0
    default_value_child: int = 0
    is_confirm_child: bool = False


class WaitingEstimate(MatocaModel):
    minutes: int
    is_more: bool = False
```

Extend `Shop` with:

```python
forms: ShopForms | None = None
options: ShopOptions = Field(default_factory=ShopOptions)
waiting_time: WaitingEstimate | None = None
is_issuable: bool = False
is_open: bool = False
is_issuable_area: bool = False
next_reception_time: str | None = None
ticketing_button_text: str | None = None
```

Import `Field` from Pydantic. Do not model every captured message or confirmation-item field yet; `extra="allow"` preserves them until the write-flow requirements are designed.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/unit/matoca/test_shop_models.py tests/unit/matoca/test_client.py -v
uv run ruff check src/matoca_service/matoca tests/unit/matoca
uv run mypy src/matoca_service/matoca
```

Expected: all commands pass, and no test output contains a credential-shaped value.

- [ ] **Step 5: Commit the model boundary**

```bash
git add src/matoca_service/matoca/models.py tests/unit/matoca/test_shop_models.py
git commit -m "feat: model matoca shop details"
```

---

### Task 2: Shop and Waiting Detail Client Methods

**Files:**
- Modify: `src/matoca_service/matoca/client.py:1-92`
- Modify: `src/matoca_service/matoca/models.py:26-27`
- Modify: `tests/unit/matoca/test_client.py:1-75`

**Interfaces:**
- Consumes: `MatocaClient`'s existing base URL, LIFF Bearer headers, and successful-envelope parser.
- Produces: `MatocaClient.get_shop(shop_id: int) -> Shop`, `MatocaClient.get_waiting(waiting_id: int) -> Waiting`, and non-empty `list_waiting() -> list[Waiting]` parsing.

- [ ] **Step 1: Write a failing client test for shop details**

```python
@pytest.mark.asyncio
@respx.mock
async def test_get_shop_reads_content_shop(merchant: MerchantConfig) -> None:
    route = respx.get("https://admin.junbanmachi.jp/liff/shops/3272").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": {
                    "shop": {
                        "id": 3272,
                        "name": "synthetic merchant",
                        "waiting_time": {"minutes": 90, "is_more": True},
                    }
                },
            },
        )
    )

    async with httpx.AsyncClient() as http:
        shop = await MatocaClient(merchant, http, "synthetic-liff").get_shop(3272)

    assert route.calls[0].request.headers["authorization"] == "Bearer synthetic-liff"
    assert shop.id == 3272
    assert shop.waiting_time is not None
    assert shop.waiting_time.minutes == 90
```

- [ ] **Step 2: Run the shop-detail test and verify RED**

Run: `uv run pytest tests/unit/matoca/test_client.py::test_get_shop_reads_content_shop -v`

Expected: FAIL because `MatocaClient.get_shop` does not exist.

- [ ] **Step 3: Implement `get_shop` with strict envelope shape checking**

```python
async def get_shop(self, shop_id: int) -> Shop:
    response = await self._http.get(
        f"{self._base_url}/liff/shops/{shop_id}",
        headers=self._headers,
    )
    payload = await self._json(response)
    content = payload.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("shop"), dict):
        raise MatocaApiError("Matoca shop response has an invalid content shape")
    return Shop.model_validate(content["shop"])
```

- [ ] **Step 4: Write failing tests for a non-empty waiting list and waiting detail**

```python
@pytest.mark.asyncio
@respx.mock
async def test_waiting_parses_non_empty_content(merchant: MerchantConfig) -> None:
    respx.get("https://admin.junbanmachi.jp/liff/waiting").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": [{"id": 125000001, "count": 72, "number": 87}],
            },
        )
    )
    async with httpx.AsyncClient() as http:
        waiting = await MatocaClient(merchant, http, "synthetic-liff").list_waiting()
    assert waiting[0].id == 125000001
    assert waiting[0].count == 72
    assert waiting[0].number == 87


@pytest.mark.asyncio
@respx.mock
async def test_get_waiting_reads_content_object(merchant: MerchantConfig) -> None:
    respx.get("https://admin.junbanmachi.jp/liff/waiting/125000001").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": {"id": 125000001, "count": 72, "number": 87},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        waiting = await MatocaClient(merchant, http, "synthetic-liff").get_waiting(125000001)
    assert waiting.id == 125000001
```

The numeric values are synthetic structural fixtures. They must not be copied from a live account response.

- [ ] **Step 5: Run both waiting tests and verify RED**

Run:

```bash
uv run pytest tests/unit/matoca/test_client.py::test_waiting_parses_non_empty_content -v
uv run pytest tests/unit/matoca/test_client.py::test_get_waiting_reads_content_object -v
```

Expected: the list test fails because `Waiting` has no typed `count`/`number`, and the detail test fails because `get_waiting` does not exist.

- [ ] **Step 6: Extend the waiting model conservatively**

```python
class Waiting(MatocaModel):
    id: int
    count: int | None = None
    number: int | None = None
    shop_id: int | str | None = None
    adult_count: int | None = None
    child_count: int | None = None
    status: str | None = None
```

Only `id` remains required because the current private captures do not prove that all list and detail variants include the same fields. Unknown fields remain available through `model_extra`.

- [ ] **Step 7: Implement `get_waiting` with shape validation**

```python
async def get_waiting(self, waiting_id: int) -> Waiting:
    response = await self._http.get(
        f"{self._base_url}/liff/waiting/{waiting_id}",
        headers=self._headers,
    )
    payload = await self._json(response)
    content = payload.get("content")
    if not isinstance(content, dict):
        raise MatocaApiError("Matoca waiting response has an invalid content shape")
    return Waiting.model_validate(content)
```

- [ ] **Step 8: Add invalid-envelope tests**

Parameterize `get_shop` with missing/non-object `content.shop` and `get_waiting` with missing/list `content`. Assert `MatocaApiError` and the stable redacted messages shown in the implementations; never include response bodies in exception text.

- [ ] **Step 9: Run the full client test module and static checks**

Run:

```bash
uv run pytest tests/unit/matoca/test_client.py -v
uv run ruff check src/matoca_service/matoca tests/unit/matoca
uv run ruff format --check src/matoca_service/matoca tests/unit/matoca
uv run mypy src/matoca_service/matoca
```

Expected: all commands pass.

- [ ] **Step 10: Commit the read-only client methods**

```bash
git add src/matoca_service/matoca/client.py src/matoca_service/matoca/models.py tests/unit/matoca/test_client.py
git commit -m "feat: read matoca shop and waiting details"
```

---

### Task 3: Authenticated Detail Service and Web APIs

**Files:**
- Modify: `src/matoca_service/service.py:28-85`
- Modify: `src/matoca_service/web/app.py:1-57`
- Modify: `tests/unit/test_service.py`
- Modify: `tests/unit/web/test_app.py`

**Interfaces:**
- Consumes: `MatocaClient.get_shop`, `MatocaClient.get_waiting`, the configured merchant map, `TokenManager.ensure_native_token`, and `TokenManager.ensure_liff_token`.
- Produces: `MatocaService.shop_detail(merchant_key: str, shop_id: int) -> Shop`, `MatocaService.waiting_detail(merchant_key: str, waiting_id: int) -> Waiting`, `GET /api/shops/{shop_id}`, and `GET /api/waiting/{waiting_id}`.

- [ ] **Step 1: Write a failing service test for one-time LIFF recovery on detail reads**

Add a respx-based test modeled on `test_dashboard_reissues_liff_once_after_matoca_unauthorized`, but call `shop_detail("sawayaka", 3272)`. Return 401 for the first `/liff/auth`, issue a synthetic fresh LIFF token through the existing monkeypatch, return success for the second auth and shop-detail request, then assert exactly two auth attempts and one detail request.

The test must use synthetic JWT claims and token strings only. It must assert the persisted cache contains the synthetic fresh token without printing it.

- [ ] **Step 2: Run the service detail test and verify RED**

Run: `uv run pytest tests/unit/test_service.py::test_shop_detail_reissues_liff_once_after_unauthorized -v`

Expected: FAIL because `MatocaService.shop_detail` does not exist.

- [ ] **Step 3: Extract one private authenticated read helper**

Add a generic private helper to `MatocaService`:

```python
async def _authenticated_read(
    self,
    merchant_key: str,
    operation: Callable[[MatocaClient], Awaitable[T]],
) -> T:
```

It must:

1. Resolve the merchant before opening HTTP.
2. Acquire the existing `_operation_lock` in the public caller.
3. Create the existing HTTP, refresh, LIFF, and Matoca clients.
4. Ensure Native state without `force=True`.
5. Authenticate and run `operation` with the cached LIFF token.
6. On HTTP 401/403 only, acquire a fresh LIFF token with `force=True`, authenticate, and run the operation once more.
7. Propagate every other error without embedding credentials in a new message.

Refactor `dashboard` to use the same retry boundary only if doing so keeps its existing concurrency and response behavior unchanged; otherwise leave dashboard untouched in this task.

- [ ] **Step 4: Implement public detail service methods**

```python
async def shop_detail(self, merchant_key: str, shop_id: int) -> Shop:
    async with self._operation_lock:
        return await self._authenticated_read(
            merchant_key,
            lambda client: client.get_shop(shop_id),
        )


async def waiting_detail(self, merchant_key: str, waiting_id: int) -> Waiting:
    async with self._operation_lock:
        return await self._authenticated_read(
            merchant_key,
            lambda client: client.get_waiting(waiting_id),
        )
```

Add the required `Awaitable`, `Callable`, `TypeVar`, and `Protocol` typing imports without weakening MyPy settings.

- [ ] **Step 5: Run service tests and verify GREEN**

Run: `uv run pytest tests/unit/test_service.py -v`

Expected: all service tests pass, including existing dashboard serialization and retry coverage.

- [ ] **Step 6: Write failing FastAPI endpoint tests**

Extend the fake service with `shop_detail` and `waiting_detail`. Add tests that request:

```text
GET /api/shops/3272?merchant=sawayaka
GET /api/waiting/125000001?merchant=sawayaka
```

Assert status 200 and typed JSON fields. Also assert FastAPI returns 422 for zero or negative IDs, so invalid IDs never reach the service.

- [ ] **Step 7: Run the endpoint tests and verify RED**

Run: `uv run pytest tests/unit/web/test_app.py -v`

Expected: FAIL with 404 for the two new routes.

- [ ] **Step 8: Extend the service protocol and add read-only routes**

Add to the Web service protocol:

```python
async def shop_detail(self, merchant_key: str, shop_id: int) -> Shop: ...
async def waiting_detail(self, merchant_key: str, waiting_id: int) -> Waiting: ...
```

Add routes:

```python
@app.get("/api/shops/{shop_id}", response_model=Shop)
async def shop_detail_api(
    shop_id: int = Path(ge=1),
    merchant: str = Query(default="sawayaka"),
) -> Shop:
    return await dashboard_service.shop_detail(merchant, shop_id)


@app.get("/api/waiting/{waiting_id}", response_model=Waiting)
async def waiting_detail_api(
    waiting_id: int = Path(ge=1),
    merchant: str = Query(default="sawayaka"),
) -> Waiting:
    return await dashboard_service.waiting_detail(merchant, waiting_id)
```

Import `Path`, `Shop`, and `Waiting`. Do not add POST or DELETE routes in this task.

- [ ] **Step 9: Run all offline verification**

Run:

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

Expected: all tests and static checks pass. No command contacts LINE or Matoca because all HTTP tests use respx/ASGI transports.

- [ ] **Step 10: Restart and verify only the static service surface**

Run:

```bash
supervisorctl restart matoca
supervisorctl status matoca
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' http://192.168.10.103:48173/static/dashboard.css
```

Expected: Supervisor reports `RUNNING` after `startsecs`, and curl prints `200`. Do not call `/`, `/api/dashboard`, or either new detail API as part of deployment verification because those routes can contact real authentication and Matoca services.

- [ ] **Step 11: Commit the authenticated read surface**

```bash
git add src/matoca_service/service.py src/matoca_service/web/app.py tests/unit/test_service.py tests/unit/web/test_app.py
git commit -m "feat: expose matoca detail APIs"
```

---

## Follow-Up Plans

After this plan is implemented and reviewed, write separate plans in this order:

1. **Queue mutation safety and client APIs:** typed create request/response, cancellation response, CSRF/Origin enforcement, explicit preview/confirm boundary, offline tests, and a production guard that prevents accidental live submission. No live mutation validation without renewed user approval.
2. **Queue Web workflow:** shop detail page, form rules from the read API, confirmation screen, active waiting detail, cancellation confirmation, responsive verification, and no nested-card UI.
3. **Authentication resilience and redaction:** centralized secret redaction, token-expiry cases, 401/403 boundaries, refresh failures, state-write failures, and restart/cache recovery.
4. **Merchant portability:** remove the default `sawayaka` assumption from routes, provide configured merchant selection, validate merchant keys without leaking configuration internals, and document adding a merchant.

The real Native Refresh rotation and real queue create/cancel operations remain explicit manual integration checkpoints, never automated test steps.
