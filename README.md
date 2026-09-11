# Matoca Service Design

Date: 2026-09-10

## Current Status

The working implementation currently supports:

- Structural validation of a configured LINE native access/refresh pair.
- Atomic native credential rotation through the captured Thrift Compact
  Protocol endpoint.
- LIFF access-token issuance and per-LIFF caching.
- Matoca shop details, waiting status, queue creation, and cancellation.
- A responsive Japanese Web console for scanning and operating supported merchants.

## Supported Merchants

| Merchant | Status | Capabilities |
| --- | --- | --- |
| 炭焼きレストラン さわやか | Supported | Shop availability, wait estimates, join queue, current queue, cancellation |
| ラ・オハナ 横浜本牧 | Supported | Shop availability, wait estimates, join queue, current queue, cancellation |

Supported merchants are defined by the tracked internal registry
`src/matoca_service/merchant_registry.toml`. Users configure LINE client metadata and
authentication state, not merchant endpoints. A new merchant is added only after its
authentication and queue protocol have been verified from captures.

The real Sawayaka flow was verified on 2026-09-10:

```text
configured LINE native pair
  -> issueLiffView
  -> LIFF access token
  -> POST /liff/auth: 200 success
  -> GET /liff/shops: 200, real shop data
```

Matoca directly reuses the LIFF access token as its Bearer credential. The
observed `/liff/auth` response does not issue a Matoca cookie, session, or
replacement JWT.

## Quick Start

Install `uv`, then run:

```bash
uv python install 3.14
uv sync --all-groups
cp .env.example .env
cp line_client.example.toml line_client.toml
cp state.example.json state.json
chmod 600 .env line_client.toml state.json
```

Replace only the placeholder values in `state.json`:

```json
{
  "version": 1,
  "line": {
    "access_token": "current LINE native access token",
    "refresh_token": "matching LINE native refresh token",
    "access_expires_at": null,
    "refresh_expires_at": null,
    "rtid": null,
    "aid": null,
    "lsid": null,
    "adid": "LINE device advertising identifier",
    "updated_at": null,
    "pending_access_report": false
  },
  "liff_tokens": {}
}
```

Start the Web UI:

```bash
uv run matoca-web
```

The default address is:

```text
http://127.0.0.1:48173
```

## Manual Web Console

The Web UI is a manual queue console for the supported merchants. The first page asks
the user to choose a `加盟店`; it currently lists `炭焼きレストラン さわやか` and
`ラ・オハナ 横浜本牧`.

On a merchant page:

- The initial filter is `受付中のみ`, so shops that can accept a queue request are shown
  first without extra interaction.
- Every shop row shows the current number of waiting groups and Matoca's `公式目安`.
- Global party settings are available from the header. New installations default to
  `成人 2 人` and `子供 0 人`; each shop's live form limits are still applied when the
  join dialog opens.
- Joining and cancelling are explicit `手動操作`. This phase does not schedule a future
  arrival time or automatically submit a queue request.

店舗一覧は SQLite キャッシュから表示され、バックグラウンド収集とは別に安全に閲覧
できます。現在の順番待ちは Matoca API から独立して更新されるため、店舗一覧の再読込で
進行中の順番待ちが消えることはありません。ヘッダーの更新ボタンは、必要なときだけ店舗
情報の手動更新を要求します。

For a private server, bind to the private interface used by Cloudflare Tunnel
or another trusted reverse proxy. The application intentionally does
not implement user login because the deployment is expected to be protected
by the external access-control layer.

## Goal

Build a portable, single-user service that can run locally or on a Linux
server and:

- Starts from a manually configured LINE native access-token and
  refresh-token pair.
- Automatically rotates and persists both LINE tokens.
- Obtains LIFF access tokens for configured merchants.
- Later uses LIFF access tokens to explore and call Matoca APIs.
- Supports Sawayaka first without hard-coding Sawayaka into protocol clients.
- Can expose a Web UI behind an external access-control layer such as
  Cloudflare Zero Trust.

The initial implementation established LINE authentication first. Read-only
Matoca operations and the Web UI now build on that authenticated client.

This system cannot guarantee permanent operation. LINE logout, device
revocation, account restrictions, token revocation, or private protocol
changes can require manual recovery or a new token pair.

## Runtime Boundary

All paths in this document are relative to the repository root unless stated
otherwise. The application does not depend on a specific checkout directory,
operating-system user, or hosting provider.

The Web service runs as one process and listens on a configurable address.
Bind it only to the private interface reached by the trusted reverse proxy or
access-control tunnel; it does not accept a CIDR setting itself.

The application never displays raw LINE or LIFF tokens in the Web UI, logs,
test reports, or exception messages.

## Python and Package Management

The project uses `uv` for:

- Installing and managing CPython.
- Creating `.venv`.
- Resolving and locking dependencies.
- Running tests, tools, scripts, and the application.

The operating system's Python installation is not used by the project.

Pinned interpreter:

```text
CPython 3.14
```

Repository files:

```text
.python-version
pyproject.toml
uv.lock
uv.toml
```

Expected settings:

```text
.python-version: 3.14
requires-python: >=3.14,<3.15
python-preference: only-managed
```

All commands run through `uv`, for example:

```bash
uv python install 3.14
uv sync --all-groups
uv run pytest
uv run matoca-line status
```

Direct use of `/usr/bin/python`, `python3`, `pip`, or a manually created
virtual environment is outside the supported workflow.

## Technology

Phase 1:

- CPython 3.14 managed by `uv`.
- HTTPX with HTTP/2 support.
- Apache Thrift Compact Protocol.
- Pydantic v2 for state and protocol result validation.
- Typer for diagnostic and administrative CLI commands.
- pytest, pytest-asyncio, respx, coverage, Ruff, and mypy.
- `fcntl.flock` and atomic file replacement for state persistence.

Web interface:

- FastAPI and Uvicorn with one worker.
- Jinja2 server-rendered HTML.

The single-user, single-instance runtime requires a local SQLite database and creates it
automatically at the configured database path.

## SQLite Storage and Collection

`MATOCA_DATABASE_FILE` selects the SQLite database file and defaults to
`./data/matoca.db`. The ignored `data/` directory is created with mode `0700`; the
database and its SQLite WAL/SHM sidecars are enforced as mode `0600`.

The collection coordinator makes read-only merchant requests adaptively: it polls a
merchant without history every five minutes, polls during its learned operating window
every minute, and polls outside that window every 15 minutes. It never overlaps requests
for the same merchant and honors merchant-specific rate-limit backoff.

Manual refreshes and requests without a cached catalog share the same admission and
durable backoff. Detail-level rate limits preserve the list and any completed detail
observations before recording the retry deadline. Static identity fields refresh once
per Tokyo calendar day; live observations and queue forms continue updating each cycle.
Complete catalogs replace current membership, including an empty catalog. Partial
catalogs preserve known members with a stale status; historical observations remain
available after a shop leaves the current catalog.

SQLite operations run outside the event loop. Transient storage failures retry after
60 seconds with sanitized logging. Shutdown allows 0.1 seconds for normal completion,
then cancels collection and allows up to 10 seconds to drain outstanding storage; failure
to drain raises a shutdown error. Configure Supervisor's stop timeout above this bound.
Schema version 5 upgrades versions 1–4 additively and records catalog timing and membership.

Raw minute observations remain in SQLite for 180 days. Daily maintenance converts older
fresh observations into five-minute waiting-group and waiting-time aggregates before
deleting the corresponding raw rows.

The offline frontend integration test runs the actual merchant script against synthetic
DOM and HTTP boundaries and requires Node.js 22 or newer on the test host. Node.js is not
a runtime dependency of the Web service.

## Repository-Local Configuration

All runtime configuration and state are stored in the repository root by
default. Paths can be overridden through environment variables when a
packager or deployment needs a different layout. Real configuration files
are ignored by Git.

Ignored files:

```text
.env
line_client.toml
state.json
state.lock
data/
events.jsonl
*.tmp
```

Tracked templates:

```text
.env.example
line_client.example.toml
state.example.json
```

`.gitignore` must also exclude:

```text
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
htmlcov/
coverage.xml
```

File permissions:

```text
.env        0600
line_client.toml 0600
state.json  0600
```

### `.env`

`.env` contains fixed process configuration only:

```dotenv
MATOCA_LINE_CLIENT_FILE=./line_client.toml
MATOCA_STATE_FILE=./state.json
MATOCA_DATABASE_FILE=./data/matoca.db
MATOCA_LOG_LEVEL=INFO
MATOCA_HOST=127.0.0.1
MATOCA_PORT=48173
```

It does not contain LINE access tokens, refresh tokens, LIFF tokens, or other
rotating authentication data.

### `line_client.toml`

`line_client.toml` contains device-dependent LINE client metadata:

```toml
host = "legy-jp.line-apps.com"
application = "ANDROIDSECONDARY\t26.11.0\tAndroid OS\t14"
locale = "en_US"
protocol_version = "1"
user_agent = "Line/26.11.0"
```

Merchant definitions remain tracked in `src/matoca_service/merchant_registry.toml` as part
of the project source. SQLite data is stored in `data/matoca.db` and is never committed.

### `state.json`

`state.json` is the only source of mutable authentication state.

The user creates it once before the first run:

```json
{
  "version": 1,
  "line": {
    "access_token": "...",
    "refresh_token": "...",
    "access_expires_at": null,
    "refresh_expires_at": null,
    "rtid": null,
    "aid": null,
    "lsid": null,
    "adid": "...",
    "updated_at": null,
    "pending_access_report": false
  },
  "liff_tokens": {}
}
```

On first load, the service decodes the configured JWTs, validates that the
access and refresh tokens belong together, derives claims such as `aid`,
`lsid`, `rtid`, and expiry times, and atomically normalizes the state file.

There is no bootstrap environment file and no import or migration from
`.env`.

## Atomic Token Rotation

The captured refresh request requires:

```text
Header: old x-line-access
Body: old Refresh Token
```

The refresh response returns:

```text
new x-line-access
new Refresh Token
```

The two returned tokens form one state transition and are never persisted
separately.

Update sequence:

1. Acquire an exclusive `state.lock` using `fcntl.flock`.
2. Reload `state.json` after acquiring the lock.
3. Re-evaluate whether refresh is still required.
4. Send at most one refresh request.
5. Decode and validate both returned JWTs.
6. Verify token relationships, identifiers, and expiry ordering.
7. Build a complete replacement state document in memory.
8. Write it to a temporary file in the repository directory.
9. Flush and `fsync` the file.
10. Set mode `0600`.
11. Atomically replace `state.json` with `os.replace`.
12. `fsync` the parent directory.
13. Release the lock.

The new token pair is persisted before reporting the refreshed access token
or making any LIFF request.

An ambiguous network failure during `refresh` is not retried automatically.
The old refresh token might already have been consumed. The CLI reports a
recovery-required state without printing either token.

## Captured Native Refresh Flow

Endpoint:

```text
POST https://legy-jp.line-apps.com/EXT/auth/tokenrefresh/v1
Content-Type: application/x-thrift
Thrift Compact Protocol
```

Sequence:

```text
reportRefreshedAccessToken(old access token)
refresh(old refresh token)
    -> new access token
    -> new refresh token
reportRefreshedAccessToken(new access token)
```

The successful capture proves:

- Native access-token lifetime is seven days.
- The refresh-token JWT is rotated.
- Refresh-token expiry rolls forward approximately one year.
- Refresh-token `jti` remains the credential-family identifier.
- Refresh-token `ati` changes to the new access-token `jti`.
- Refresh-token `rot` is `ROTATE`.

The implementation uses structured Thrift Compact Protocol reads and writes.
It must not scan binary payloads for strings beginning with `eyJ`.

Unknown response fields are skipped according to their Thrift types so future
additive protocol changes do not break token extraction.

## Captured LIFF Issuance Flow

Endpoint:

```text
POST https://legy-jp.line-apps.com/LIFF1
Content-Type: application/x-thrift
Thrift Compact Protocol
Thrift method: issueLiffView
```

Headers:

```text
x-line-access: current native access token
x-line-liff-id: merchant LIFF ID
x-line-application: configured native application metadata
x-lal: configured locale
x-lpv: configured protocol version
```

The request includes account/device data and merchant-specific LIFF entry
data. Values derivable from token claims are derived rather than duplicated
in configuration. Device values that are not derivable, including `adid`,
remain in `state.json`.

Returned LIFF access tokens are stored in:

```text
state.json -> liff_tokens -> <liff_id>
```

The LIFF token manager supports:

- Obtaining a LIFF token for a configured merchant.
- Returning a cached token while valid.
- Forcing one renewal.
- Keeping tokens isolated by LIFF ID.
- Never logging or returning token text through diagnostics.

## Phase 1 Scope: LINE Authentication

Phase 1 delivers only:

1. `uv` project and managed CPython setup.
2. Configuration and state models.
3. JWT decoding and relationship validation.
4. Atomic JSON state store and file locking.
5. Thrift Compact Protocol structures for:
   - `refresh`
   - `reportRefreshedAccessToken`
   - `issueLiffView`
6. Native access and refresh token rotation.
7. LIFF access-token issuance and per-LIFF caching.
8. CLI diagnostics with redacted output.
9. Offline unit tests.
10. Explicit live integration tests using the ignored `state.json`.

Matoca shop, waiting, queue creation, cancellation, and Web UI modules are
not part of Phase 1.

## Phase 1 CLI

Planned commands:

```bash
uv run matoca-line state validate
uv run matoca-line state status
uv run matoca-line token refresh
uv run matoca-line token ensure
uv run matoca-line liff issue sawayaka
uv run matoca-line liff status
```

Example redacted status:

```text
Native access token: valid
Access expiry: 2026-09-17T09:47:18+09:00
Refresh token: present
Refresh expiry: 2027-09-10T09:47:18+09:00
Credential family: 15cf1ed2...8bed7
LIFF sawayaka: valid
```

## Testing Requirements

### Offline tests

- Thrift message header, field ID, type, and nesting tests.
- Golden-byte request tests built with synthetic, non-secret tokens.
- Captured-response-shape tests using fully sanitized fixtures.
- JWT header and payload decoding tests.
- Access and refresh token relationship tests:
  - refresh `jti` equals access `rtid`
  - refresh `ati` points to the associated access `jti`
  - `aud`, `scp`, `aid`, `lsid`, and application metadata agree
- Expiry and refresh-threshold tests.
- Unknown Thrift field skipping tests.
- Atomic replacement, permissions, `fsync`, and lock-contention tests.
- Tests proving tokens are redacted from logs and exceptions.
- Tests proving ambiguous refresh failures are not retried.
- Tests proving a returned token pair is persisted before reporting or LIFF
  issuance.

### Live integration tests

Live tests are opt-in and excluded from the default test command:

```bash
uv run pytest
uv run pytest -m live
```

Rules for live tests:

- Read credentials only from ignored `state.json`.
- Acquire the same exclusive state lock as production.
- Never run in parallel.
- Save rotated tokens atomically.
- Never print request or response bodies containing credentials.
- A destructive refresh test runs only when explicitly selected.
- LIFF issuance may run independently while the native token remains valid.

Successful live tests establish that the implementation reproduces the
captured protocol against the real LINE service.

## Post-Phase-1 Interface Exploration

After all Phase 1 offline tests pass and live refresh plus LIFF issuance are
verified, the agent may use the authorized credentials in `state.json` to
explore Matoca behavior.

Exploration no longer depends on Charles.

Use, in order:

1. Direct HTTP calls with the issued LIFF access token for known Matoca APIs.
2. An agent-controlled browser when JavaScript execution, navigation state,
   geolocation, or UI-generated payloads must be observed.
3. Sanitized JSON fixtures committed to the repository.

Exploration starts with read-only calls. State-changing actions such as
joining or cancelling a real queue require explicit user authorization for
that operation.

Target operations:

```text
POST   /liff/auth
GET    /liff/shops
GET    /liff/shops/{shop_id}
GET    /liff/waiting
GET    /liff/waiting/{waiting_id}
POST   /liff/waiting
DELETE /liff/waiting/{waiting_id}
```

The agent records:

- Request method, path, query, headers, and JSON body.
- Response status and schema.
- Authentication failure behavior.
- Active waiting-state schemas.
- Cancellation behavior.
- Duplicate and suspended-shop errors.
- LIFF token expiry and renewal behavior.

No raw tokens are committed to Git.

## Later Architecture

After protocol exploration, later modules are added:

```text
src/matoca_service/
  matoca/
    client.py
    models.py
    service.py
  merchants/
    models.py
    registry.py
  web/
    routes.py
    templates/
    static/
  jobs/
    waiting_poll.py
```

The Web UI is designed to run behind a trusted access-control layer. Queue operations
use the selected shop coordinates supplied by Matoca and do not request browser geolocation.

## Initial Project Structure

Phase 1 creates:

```text
.
  .gitignore
  .python-version
  uv.toml
  pyproject.toml
  uv.lock
  README.md
  .env.example
  config.example.toml
  state.example.json
  src/
    matoca_service/
      __init__.py
      config.py
      cli.py
      line/
        __init__.py
        jwt.py
        models.py
        thrift_codec.py
        refresh.py
        liff.py
        token_manager.py
      state/
        __init__.py
        models.py
        store.py
        locking.py
  tests/
    fixtures/
    unit/
    integration/
```

## Delivery Phases

1. Rewrite and approve this design.
2. Create a detailed implementation plan.
3. Build project foundation and state handling with tests.
4. Implement and test native LINE token refresh.
5. Implement and test LIFF issuance.
6. Run explicit live LINE integration tests.
7. Use the authenticated client to explore Matoca APIs.
8. Design and implement the generic Matoca client.
9. Add the Web UI and optional deployment examples.
