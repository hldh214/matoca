# LINE Authentication Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a portable Python client that safely rotates LINE native credentials and issues cached LIFF access tokens from repository-local configuration.

**Architecture:** Separate pure token/protocol parsing from network clients and state mutation. A single token manager owns the refresh and LIFF workflows while `JsonStateStore` serializes every mutable credential transition under an exclusive lock and atomic file replacement.

**Tech Stack:** uv-managed CPython 3.14, HTTPX with HTTP/2, Apache Thrift Compact Protocol, Pydantic v2, Typer, pytest, pytest-asyncio, respx, coverage, Ruff, and mypy.

**Spec:** `README.md`

## Global Constraints

- Use CPython `3.14` managed only by `uv`; set `requires-python = ">=3.14,<3.15"` and `python-preference = "only-managed"`.
- Store fixed settings in `.env` and `config.toml`; store every mutable credential only in ignored `state.json`.
- Never commit, log, display, or include real LINE or LIFF tokens in exceptions and test reports.
- Use structured Thrift Compact Protocol reads and writes; never scan binary payloads for JWT-looking strings.
- Persist a returned native access/refresh pair atomically before reporting it or issuing a LIFF token.
- Never automatically retry an ambiguous native refresh failure.
- Default tests are offline; real LINE tests are marked `live`, serialized by the production state lock, and explicitly selected.
- Phase 1 contains no Matoca shop, waiting, queue, cancellation, or Web UI implementation.

---

### Task 1: uv Project Foundation

**Files:**
- Create: `.gitignore`
- Create: `.python-version`
- Create: `uv.toml`
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `config.example.toml`
- Create: `state.example.json`
- Create: `src/matoca_service/__init__.py`
- Create: `tests/unit/test_project_contract.py`

**Interfaces:**
- Consumes: The configuration contract in `README.md`.
- Produces: The `matoca-line` console script and an installable `matoca_service` package.

- [ ] **Step 1: Write the failing project-contract test**

```python
def test_runtime_templates_never_contain_credentials(repo_root):
    env_text = (repo_root / ".env.example").read_text()
    assert "access_token" not in env_text.lower()
    assert "refresh_token" not in env_text.lower()


def test_mutable_files_are_gitignored(repo_root):
    ignored = (repo_root / ".gitignore").read_text().splitlines()
    assert {"state.json", "state.lock", "config.toml", ".env"} <= set(ignored)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest tests/unit/test_project_contract.py -v`

Expected: FAIL because the project metadata and templates do not exist.

- [ ] **Step 3: Add the minimal project and templates**

Define runtime dependencies `httpx[http2]`, `pydantic`, `pydantic-settings`, `thrift`, and `typer`; define development dependencies `pytest`, `pytest-asyncio`, `respx`, `coverage`, `ruff`, and `mypy`. Configure the `matoca-line = "matoca_service.cli:app"` entry point and strict Ruff/mypy settings.

- [ ] **Step 4: Install the managed interpreter and lock dependencies**

Run:

```bash
uv python install 3.14
uv lock
uv sync --all-groups
uv run pytest tests/unit/test_project_contract.py -v
```

Expected: CPython 3.14 is installed by uv, the lockfile is created, and the contract tests pass.

- [ ] **Step 5: Commit**

```bash
git add .gitignore .python-version uv.toml pyproject.toml uv.lock .env.example config.example.toml state.example.json src tests/unit/test_project_contract.py
git commit -m "build: initialize uv python project"
```

### Task 2: Configuration, JWT, and State Models

**Files:**
- Create: `src/matoca_service/config.py`
- Create: `src/matoca_service/line/jwt.py`
- Create: `src/matoca_service/line/models.py`
- Create: `src/matoca_service/state/models.py`
- Create: `tests/unit/line/test_jwt.py`
- Create: `tests/unit/state/test_models.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: `decode_jwt_unverified(token: str) -> JwtParts`, `validate_native_pair(access_token: str, refresh_token: str) -> NativePairClaims`, `AppConfig`, `AppState`, `LineState`, and `LiffTokenState`.

- [ ] **Step 1: Write failing tests for JWT decoding and pair validation**

```python
def test_native_pair_links_refresh_family_and_access_token(jwt_factory):
    access = jwt_factory({"jti": "access-1", "rtid": "family-1", "aud": "LINE"})
    refresh = jwt_factory({"jti": "family-1", "ati": "access-1", "aud": "LINE"})
    claims = validate_native_pair(access, refresh)
    assert claims.rtid == "family-1"


def test_native_pair_rejects_mismatched_ati(jwt_factory):
    access = jwt_factory({"jti": "access-1", "rtid": "family-1", "aud": "LINE"})
    refresh = jwt_factory({"jti": "family-1", "ati": "other", "aud": "LINE"})
    with pytest.raises(TokenRelationshipError):
        validate_native_pair(access, refresh)
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/line/test_jwt.py tests/unit/state/test_models.py -v`

Expected: FAIL because the model and JWT modules do not exist.

- [ ] **Step 3: Implement strict models and unverified structural decoding**

Decode Base64URL JSON without signature forgery or trust claims. Require access `jti`, `rtid`, `iat`, `exp` and refresh `jti`, `ati`, `iat`, `exp`; validate `refresh.jti == access.rtid`, `refresh.ati == access.jti`, matching `aud`, and refresh expiry later than access expiry. Normalize derived expiry and identity metadata into `LineState`.

- [ ] **Step 4: Run focused and static checks**

Run:

```bash
uv run pytest tests/unit/line/test_jwt.py tests/unit/state/test_models.py -v
uv run ruff check src tests
uv run mypy src
```

Expected: all commands pass without exposing token text.

- [ ] **Step 5: Commit**

```bash
git add src/matoca_service/config.py src/matoca_service/line src/matoca_service/state/models.py tests
git commit -m "feat: validate line credential state"
```

### Task 3: Locked Atomic JSON State Store

**Files:**
- Create: `src/matoca_service/state/locking.py`
- Create: `src/matoca_service/state/store.py`
- Create: `tests/unit/state/test_store.py`

**Interfaces:**
- Consumes: `AppState`.
- Produces: `JsonStateStore(path: Path)`, `JsonStateStore.locked()`, `load() -> AppState`, and `save(state: AppState) -> None`.

- [ ] **Step 1: Write failing state-store tests**

```python
def test_save_atomically_replaces_state_with_mode_0600(tmp_path, valid_state):
    store = JsonStateStore(tmp_path / "state.json")
    store.save(valid_state)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load() == valid_state


def test_second_writer_waits_for_exclusive_lock(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    with store.locked():
        assert another_process_cannot_acquire(store.lock_path)
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/state/test_store.py -v`

Expected: FAIL because `JsonStateStore` is missing.

- [ ] **Step 3: Implement locking and atomic persistence**

Use `fcntl.flock(LOCK_EX)`, reload only after lock acquisition, write JSON to a same-directory temporary file, flush and `fsync`, chmod `0600`, `os.replace`, and `fsync` the parent directory. Ensure cleanup removes only the created temporary file.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/unit/state/test_store.py -v`

Expected: all permission, replacement, and contention tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/matoca_service/state tests/unit/state/test_store.py
git commit -m "feat: persist credentials atomically"
```

### Task 4: Thrift Compact Protocol Codec

**Files:**
- Create: `src/matoca_service/line/thrift_codec.py`
- Create: `tests/fixtures/line/refresh_response.bin`
- Create: `tests/fixtures/line/liff_response.bin`
- Create: `tests/unit/line/test_thrift_codec.py`

**Interfaces:**
- Produces: `encode_call(method: str, args: ThriftStruct) -> bytes`, `decode_reply(data: bytes, result_type: type[T]) -> T`, typed field descriptors, and unknown-field skipping.

- [ ] **Step 1: Write failing golden-byte and unknown-field tests**

```python
def test_refresh_call_matches_sanitized_golden_bytes():
    encoded = encode_refresh_call("synthetic-refresh-token")
    assert encoded == REFRESH_REQUEST_GOLDEN


def test_reply_decoder_skips_unknown_nested_fields():
    result = decode_refresh_reply(REFRESH_REPLY_WITH_UNKNOWN_FIELDS)
    assert result.access_token == "synthetic-access-token"
    assert result.refresh_token == "synthetic-refresh-token"
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/line/test_thrift_codec.py -v`

Expected: FAIL because structured encoding and decoding are missing.

- [ ] **Step 3: Implement typed Compact Protocol traversal**

Use Apache Thrift transports/protocols to write message headers, structs, field IDs, values, and stops. Decode only declared fields, call the protocol skip operation for unknown types, and reject replies with missing credentials or Thrift application exceptions.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/unit/line/test_thrift_codec.py -v`

Expected: golden requests match and additive unknown fields are tolerated.

- [ ] **Step 5: Commit**

```bash
git add src/matoca_service/line/thrift_codec.py tests/fixtures/line tests/unit/line/test_thrift_codec.py
git commit -m "feat: add structured line thrift codec"
```

### Task 5: Native Refresh Client and Token Manager

**Files:**
- Create: `src/matoca_service/line/refresh.py`
- Create: `src/matoca_service/line/token_manager.py`
- Create: `tests/unit/line/test_refresh.py`
- Create: `tests/unit/line/test_token_manager.py`

**Interfaces:**
- Produces: `LineRefreshClient.refresh(access_token, refresh_token) -> NativeTokenPair`, `report_refreshed_access_token(access_token) -> None`, and `TokenManager.ensure_native_token(force: bool = False) -> NativeTokenStatus`.

- [ ] **Step 1: Write failing transport and ordering tests**

```python
@pytest.mark.asyncio
async def test_refresh_uses_old_access_header_and_old_refresh_body(respx_mock):
    route = respx_mock.post(REFRESH_URL).mock(return_value=httpx.Response(200, content=REFRESH_REPLY))
    pair = await client.refresh("old-access", "old-refresh")
    assert route.calls[0].request.headers["x-line-access"] == "old-access"
    assert decode_refresh_argument(route.calls[0].request.content) == "old-refresh"
    assert pair.access_token == "new-access"


@pytest.mark.asyncio
async def test_manager_persists_pair_before_reporting(fake_store, fake_client):
    await manager.ensure_native_token(force=True)
    assert fake_store.events == ["save:new-pair", "report:new-access"]
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/line/test_refresh.py tests/unit/line/test_token_manager.py -v`

Expected: FAIL because refresh transport and orchestration are missing.

- [ ] **Step 3: Implement the captured refresh sequence**

Send `reportRefreshedAccessToken(old access)`, then one `refresh(old refresh)` using the old `x-line-access`, validate the returned pair, atomically save it, then send `reportRefreshedAccessToken(new access)`. Do not retry refresh on timeout, disconnect, malformed response, or uncertain HTTP outcome.

- [ ] **Step 4: Verify failures, redaction, and full suite**

Run:

```bash
uv run pytest tests/unit/line/test_refresh.py tests/unit/line/test_token_manager.py -v
uv run pytest
```

Expected: call order is exact, ambiguous failures have one request attempt, and captured credentials never appear in output.

- [ ] **Step 5: Commit**

```bash
git add src/matoca_service/line/refresh.py src/matoca_service/line/token_manager.py tests/unit/line
git commit -m "feat: rotate native line credentials"
```

### Task 6: LIFF Issuance and Per-LIFF Cache

**Files:**
- Create: `src/matoca_service/line/liff.py`
- Modify: `src/matoca_service/line/token_manager.py`
- Create: `tests/unit/line/test_liff.py`

**Interfaces:**
- Produces: `LiffClient.issue_view(request: LiffViewRequest) -> LiffToken`, and `TokenManager.ensure_liff_token(merchant: MerchantConfig, force: bool = False) -> LiffTokenStatus`.

- [ ] **Step 1: Write failing LIFF request and cache tests**

```python
@pytest.mark.asyncio
async def test_issue_view_uses_current_access_and_merchant_liff_id(respx_mock):
    token = await client.issue_view(request)
    sent = respx_mock.calls[0].request
    assert sent.headers["x-line-access"] == "current-access"
    assert sent.headers["x-line-liff-id"] == "2006055787-m6P6OJ38"
    assert token.access_token == "synthetic-liff-token"


@pytest.mark.asyncio
async def test_valid_cached_liff_token_avoids_network(fake_store, fake_liff_client):
    result = await manager.ensure_liff_token(sawayaka)
    assert result.source == "cache"
    assert fake_liff_client.calls == []
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/line/test_liff.py -v`

Expected: FAIL because LIFF issuance and cache policy are missing.

- [ ] **Step 3: Implement `issueLiffView` and cache persistence**

Build the structured request from current native claims, `adid`, and merchant configuration. Decode the typed result, derive LIFF expiry from returned token data, store it under `liff_tokens[liff_id]`, and isolate renewals by LIFF ID.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/unit/line/test_liff.py -v`

Expected: headers/body match the sanitized capture shape, valid cache avoids network, and forced renewal changes only the selected LIFF entry.

- [ ] **Step 5: Commit**

```bash
git add src/matoca_service/line/liff.py src/matoca_service/line/token_manager.py tests/unit/line/test_liff.py
git commit -m "feat: issue and cache liff tokens"
```

### Task 7: Redacted CLI Diagnostics

**Files:**
- Create: `src/matoca_service/cli.py`
- Create: `tests/unit/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `matoca-line state validate`, `state status`, `token ensure`, `token refresh`, `liff issue MERCHANT`, and `liff status`.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_state_status_redacts_all_tokens(cli_runner, configured_app):
    result = cli_runner.invoke(app, ["state", "status"])
    assert result.exit_code == 0
    assert "access-secret" not in result.output
    assert "refresh-secret" not in result.output
    assert "Credential family:" in result.output
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/test_cli.py -v`

Expected: FAIL because the CLI is missing.

- [ ] **Step 3: Implement commands and safe exception rendering**

Load paths from environment, resolve the selected merchant from `config.toml`, print statuses and abbreviated non-secret identifiers, and map recovery-required failures to nonzero exit codes without request/response bodies.

- [ ] **Step 4: Verify CLI and quality gates**

Run:

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

Expected: all commands pass.

- [ ] **Step 5: Commit**

```bash
git add src/matoca_service/cli.py tests/unit/test_cli.py README.md
git commit -m "feat: add redacted line auth cli"
```

### Task 8: Opt-In Live LINE Integration Tests

**Files:**
- Create: `tests/integration/test_live_line.py`
- Create: `tests/integration/conftest.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Consumes: ignored `config.toml` and `state.json`.
- Produces: an independently selectable LIFF issuance test and an explicitly named destructive native refresh test.

- [ ] **Step 1: Add live test selection guards**

```python
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_liff_issue(live_manager, merchant):
    status = await live_manager.ensure_liff_token(merchant, force=True)
    assert status.valid


@pytest.mark.live
@pytest.mark.destructive_refresh
@pytest.mark.asyncio
async def test_live_native_refresh(live_manager):
    status = await live_manager.ensure_native_token(force=True)
    assert status.valid
```

Default collection must skip `live`; the destructive test must additionally require `--run-destructive-refresh`.

- [ ] **Step 2: Verify default tests do not access the network**

Run: `uv run pytest`

Expected: all offline tests pass and live tests are skipped.

- [ ] **Step 3: Run safe live LIFF issuance**

Run: `uv run pytest -m "live and not destructive_refresh" -v`

Expected: the real LINE endpoint accepts the generated `issueLiffView` request and the LIFF state is persisted without printing credentials.

- [ ] **Step 4: Run destructive native refresh only with explicit user authorization**

Run: `uv run pytest -m destructive_refresh --run-destructive-refresh -v`

Expected: one refresh rotates and atomically saves both native credentials, then reports the new access token.

- [ ] **Step 5: Run final verification and commit**

```bash
uv run pytest
uv run coverage run -m pytest
uv run coverage report --fail-under=90
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
git add tests/integration pyproject.toml README.md
git commit -m "test: verify live line authentication"
git push
```

Expected: offline quality gates pass; live results are documented without token material.
