# Matoca Service

A single-user Japanese queue console with persistent collection, shop history,
favorites, queue tracking, arrival-time automation and optional browser Web Push.
Runs as one Python process and one Uvicorn worker behind a trusted access-control
proxy, such as Cloudflare Zero Trust. There is no application login.

## Supported merchants

| Merchant | Status | Capabilities |
| --- | --- | --- |
| 炭焼きレストラン さわやか | Supported | Availability, estimates, manual queue/cancellation, tracking, arrival tasks |
| ラ・オハナ 横浜本牧 | Supported | Availability, estimates, manual queue/cancellation, tracking, arrival tasks |

The tracked `src/matoca_service/merchant_registry.toml` defines the supported
merchants. Adding a merchant requires capture-backed authentication and queue
protocol verification; users configure client metadata and credentials, not endpoints.

## Setup and runtime

The project uses uv-managed CPython 3.14; system Python and pip are not required.
Paths below are relative to the checkout and may be overridden with environment
variables. No machine-specific directory or operating-system account is required.

```bash
uv python install 3.14
uv sync --frozen
cp .env.example .env
cp line_client.example.toml line_client.toml
cp state.example.json state.json
chmod 600 .env line_client.toml state.json
```

Replace the placeholders in `state.json` with the matching native LINE access and
refresh tokens and device advertising identifier. Configure the captured device
client metadata in `line_client.toml`. The original version-1 `state.example.json`
remains compatible; no notification fields need to be added manually.

```bash
uv run matoca-web
```

The default UI is `http://127.0.0.1:48173`. Process configuration in `.env`:

```dotenv
MATOCA_LINE_CLIENT_FILE=./line_client.toml
MATOCA_STATE_FILE=./state.json
MATOCA_DATABASE_FILE=./data/matoca.db
MATOCA_LOG_LEVEL=INFO
MATOCA_HOST=127.0.0.1
MATOCA_PORT=48173
```

Bind only to the private interface reached by the trusted reverse proxy or access
tunnel. Run one process, one worker under Supervisor; multiple workers would create
multiple background schedulers. For an existing deployment, synchronize the locked
dependencies and restart its Supervisor program. Set the Supervisor stop timeout
above the storage/network drain bound (at least 30 seconds).

HTTP on the private LAN supports the normal console. Web Push requires the externally
configured HTTPS origin. Configure the proxy's trusted forwarding and access policy
for the same origin, including `/sw.js`, `/manifest.webmanifest`, `/static/` and `/api/`.

## Using the console

The homepage shows current personal queues and the merchant selector. A merchant
page shows cached shops, current waiting groups and the official waiting estimate.
Filter available shops or all shops, search by name, sort, and pin favorites. Shop
history includes date selection and observed minute values with visible gaps and
freshness; snapshots alone do not establish an actual call time.

`受付可能` is the initial filter and `公式目安` labels the official estimate.
店舗一覧は SQLite キャッシュから表示されます。現在の順番待ちは Matoca API から独立して更新されます。

The header settings default to two adults and zero children. Shop forms still apply
their fresh limits and confirmation fields. Manual joining and cancellation require
explicit actions. Queue admission is serialized across merchants for this account.
The initial Japanese counters are `大人 2 人` and `子ども 0 人`; `手動操作` always
requires its explicit confirmation independently of enabled arrival tasks.
If a submission result is unknown, the UI exposes reconciliation and explicit
confirmation that no queue exists; it never blindly repeats the submission.

Queue tracking persists minute observations and survives process restarts. Observed
zero groups is the agreed proxy for a call, not a claim about an unobserved upstream
status. Disappearance without a known cancellation is marked unknown. Adopted
external tickets have unknown submission time and do not become training samples.

Predictions use observed completed queue sessions and the official estimate frozen
at submission: merchant, shop, and comparable day/time samples are combined with
shrinkage and recency weighting. Cold starts use the official estimate. The UI shows
fast/typical estimates, confidence, sample count and stale/lower-bound information.
Predictions remain uncertain; neither the model nor collection predicts reception
closing times.

## Arrival-time automatic reception

Select `自動受付を設定`, enter an arrival time and explicitly check the consent box.
Only an enabled task authorizes a future automatic queue submission. The default
early tolerance and model error are both 15 minutes. The task evaluates roughly
once per minute using:

```text
now + max(0, fast prediction − model error) ≥ arrival − early tolerance
```

The runner verifies live availability, the account's current queues, form semantics
and answer limits before submitting. It allows a two-minute grace period after
arrival and otherwise expires. The UI exposes the decision, last/next evaluation,
monitoring cancellation and uncertain-result resolution. Cancelling monitoring does
not cancel an already-issued ticket; use the separate queue cancellation operation.
Restart recovery follows the durable task/intent/session linkage without replaying
ambiguous submissions. Changed forms or uncertain reads may require attention.

## Browser notifications

Open the HTTPS UI. On the homepage select `通知の設定`; on merchant pages open the
header `設定` and then `通知の設定`. Choose `通知を有効にする` and accept the browser's
permission prompt. On iPhone/iPad add the site to the Home Screen and open that web
app first. Then choose `テスト通知を送る` and confirm reception on the physical device.
Permission and physical delivery are user-initiated; automated tests do not prove
delivery to a real phone. `通知を無効にする` removes this browser's subscription.

Notifications cover automated submission success, tasks needing attention, failure
or expiry, and waiting groups at 10/5/0. A separate estimate from successive fresh
decreasing group counts notifies when the absolute predicted call target advances
by at least ten minutes. Merely waiting ten minutes does not trigger it; gaps over
ten minutes discard the trajectory baseline. This estimate is labeled as derived
from decreasing groups and is not an observed call or reception-closing forecast.

The server records notifications transactionally with task/queue changes, deduplicates
them durably, and dispatches without requiring an open page. Each subscription has
independent retries after 1/5/15 minutes; expired 404/410 endpoints are removed.
Delivery batches are limited to 20, network timeout is ten seconds, and the push
service TTL is 15 minutes. Network errors never undo business transitions.
Notification history shows locally recorded events, not proof of device reception.
Browser/OS settings, network availability and push services can delay or prevent
delivery. Always follow the shop's actual guidance.

VAPID keys are generated only on explicit enable. The private key stays in
`state.json` (0600); a fresh locked load/save preserves current LINE credentials.
The HTTPS origin used to enable notifications supplies the nonsecret VAPID subject.
Subscriptions and delivery records live in the ignored SQLite database. Endpoint
capabilities, subscription keys and upstream exception bodies never appear in the
UI or logs. The worker only opens same-origin homepage/merchant URLs and does not
cache account pages or APIs. The manifest and icons are self-contained.

Notification endpoints: `GET /api/push/public-key` is read-only;
`POST /api/push/public-key` explicitly initializes keys; `POST/DELETE
/api/push/subscriptions` register/remove a browser; `POST /api/push/test` targets
one registered browser; `GET /api/push/history` returns redacted local history.
Writes require same-origin requests.

## Storage and collection

The ignored `data/` directory uses mode 0700 and the database/WAL/SHM use 0600.
Schema version 9 upgrades earlier databases additively: cached shops/observations,
catalog membership, favorites, queue intents/sessions, arrival tasks, notification
outbox/deliveries and trajectory baselines. Authentication remains outside SQLite.
Keep the database and `state.json` together when backing up the instance.

Collection is read-only and adaptive: five minutes without history, one minute in
learned operating windows or with an active arrival task, fifteen minutes outside
the window. Merchant-specific durable backoff applies to automatic and manual reads.
Static identity refreshes daily in Tokyo time; live forms and observations refresh
each cycle. Complete catalogs replace membership; partial/error reads preserve
known data with stale status. Raw observations remain for 180 days; older fresh
observations roll up into five-minute aggregates. SQLite and state locking run off
the event loop. Real state, captures, `.env`, `line_client.toml` and `data/` are never
committed.

## Capture-backed LINE authentication

The Sawayaka read flow was verified on 2026-09-10:

```text
configured native pair → issueLiffView → LIFF access token
→ POST /liff/auth: 200 → GET /liff/shops: 200
```

Matoca directly uses the LIFF access token as its Bearer credential. The observed
`/liff/auth` response does not issue a cookie, session or replacement JWT.

Native refresh uses Thrift Compact Protocol at
`POST https://legy-jp.line-apps.com/EXT/auth/tokenrefresh/v1`, with the old access token
in `x-line-access` and the old refresh token in the body. Captured sequence:
`reportRefreshedAccessToken(old)`, `refresh`, `reportRefreshedAccessToken(new)`.
Both returned tokens are persisted as one atomic replacement under `state.lock`,
after a fresh load and relationship/expiry validation, file fsync, chmod0600 and
parent-directory fsync. Ambiguous refresh failure is not automatically retried.

The successful capture showed a seven-day native access lifetime and a rotating
refresh JWT with expiry advancing approximately one year. Refresh `jti` remains
the credential-family identifier; `ati` changes to the new access `jti`; `rot` is
`ROTATE`. Structured Thrift decoding skips unknown fields and never scans bodies
for token-like strings.

LIFF issuance uses Thrift method `issueLiffView` at
`POST https://legy-jp.line-apps.com/LIFF1`, with current `x-line-access`, merchant
`x-line-liff-id`, client application, locale and protocol headers. Device/account
fields derive from validated claims where possible; advertising identifier remains
in `state.json`. Per-LIFF tokens are cached under `liff_tokens` in that same file.

Logout, device revocation, account restrictions, revoked credentials or private
protocol changes can require a new native pair or manual recovery. Diagnostic CLI
commands are available via `uv run matoca-line --help`; tokens are redacted.

## Verification

Default tests are offline and do not require Playwright or Chromium:

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv build
```

Frontend offline behavior checks require Node.js 22 or newer on the test host; Node
and browser packages are not runtime dependencies. Browser tests are an optional
group, served from a synthetic in-memory service on loopback with all external traffic
blocked, including HTTP and WebSockets. Push tests simulate the permission/push boundary and execute worker behavior
offline; they never send real notifications or access actual queues/credentials.

```bash
uv sync --group browser
uv run --group browser python -m playwright install --with-deps chromium
uv run --group browser pytest -m browser --tracing retain-on-failure \
  --screenshot only-on-failure --full-page-screenshot
```

Failure artifacts are kept under ignored `test-results/`; open them with
`uv run --group browser playwright show-trace test-results/<test-name>/trace.zip`.
After upgrading Playwright install its matching Chromium build again. Any live
protocol verification or real queue creation/cancellation requires explicit scope;
offline tests never rotate configured credentials.
