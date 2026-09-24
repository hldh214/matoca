# Matoca Service

A single-user Japanese queue console with cached shops, favorites, immediate
queue reception, official-estimate automatic reception, current queue tracking and optional browser Web Push.
Runs as one Python process and one Uvicorn worker behind a trusted access-control
proxy, such as Cloudflare Zero Trust. There is no application login.

## Supported merchants

| Merchant | Status | Capabilities |
| --- | --- | --- |
| 炭焼きレストラン さわやか | Supported | Availability, official estimates, manual/automatic reception, cancellation, tracking |
| ラ・オハナ 横浜本牧 | Supported | Availability, official estimates, manual/automatic reception, cancellation, tracking |

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

Frontend assets use content-versioned `/assets/<version>/` URLs, including relative
JavaScript module imports. Restart the service after deploying asset changes.
Versioned assets are immutable and cacheable for a year; HTML and API responses use
`Cache-Control: no-store`. Legacy `/static/` URLs, the manifest and service worker
require cache revalidation. Apply the same access policy to `/assets/`; proxy cache
rules must respect these origin headers. An already-open tab needs a page reload
to load the new asset version.

## Using the console

The homepage shows current personal queues and the merchant selector. A merchant
page shows cached shops, current waiting groups and the official waiting estimate.
`受付可能` is the initial filter; `公式目安` is the restaurant’s estimate.
店舗一覧は SQLite キャッシュから表示されます。現在の順番待ちは Matoca API から独立して更新されます。
Default party: `大人 2 人` and `子ども 0 人`. Reception and cancellation are `手動操作`.

The initial filter shows available shops. Search by name, sort by official waiting
time or groups, and pin favorites. Current queues appear above the shop list.

The header settings default to two adults and zero children. Opening reception
loads the shop's live form, applies its limits and defaults confirmation fields.
Joining uses the shop coordinates. Immediate reception and cancellation are explicit
actions; automatic reception requires creating a schedule through `自動受付`.
Uncertain submission results are reconciled without blindly repeating a request.

The queue panel shows the ticket number, groups ahead, latest official waiting
estimate and observed status. Zero groups alone does not establish a call.
Current queue tracking and credentials survive restarts. Disappearance without a
confirmed cancellation is not treated as a confirmed call or cancellation.

The current ticket remains visible when switching merchants; cancellation always
targets the merchant that issued it. The ticket includes party size, reception
time, official call-time guidance and a map link. Manual refresh reloads the shop
list and the latest tracked queue. Failed reads preserve the last ticket and offer
retry. Favorites have their own filter, and each browser remembers search, sorting
and filtering separately for each merchant. Reception/cancellation results and
favorite-save failures are shown explicitly in Japanese.

## Official-estimate automatic reception

Choose `自動受付` next to a shop, enter the intended arrival date/time and party
details, then select `自動受付を開始`. Times use the browser timezone, with Tokyo as
the fallback. New schedules default to the dialog opening time plus the shop's live
official wait (one hour when unavailable); editing preserves the saved arrival time.
Store-specific questions and limits use the same live form as manual
reception. Scheduling is also available before reception opens via the `すべて` filter.

The server checks every minute and submits when **now + official waiting minutes
≥ arrival time**, provided the shop accepts reception and the account has no other
queue or unresolved submission. No learned model, historical samples, early margin
or prediction-error setting is used. Official times are estimates, not guaranteed
call times; the actual call may be earlier.

Before arrival, missing, stale or lower-bound (`以上`) estimates keep the task waiting.
From arrival through two minutes afterward, live reception checks may permit joining
even without an exact estimate. After this grace period the unsent task expires.
The panel shows the latest decision, last checked official estimate and next check.
You can edit unsent tasks or stop monitoring; stopping does not cancel an issued ticket.
Closed browser tabs do not stop monitoring, and new official-mode tasks survive
service restarts. Historical prediction tasks remain inactive and are never revived.
Ambiguous submission results are reconciled without automatically resending.

## Browser notifications

Open the HTTPS UI. On the homepage select `通知の設定`; on merchant pages open the
header `設定` and then `通知の設定`. Choose `通知を有効にする` and accept the browser's
permission prompt. On iPhone/iPad add the site to the Home Screen and open that web
app first. Then choose `テスト通知を送る` and confirm reception on the physical device.
Permission and physical delivery are user-initiated; automated tests do not prove
delivery to a real phone. `通知を無効にする` removes this browser's subscription.

Notifications cover waiting-group thresholds at 10/5/0. These are observations,
not predictions or proof of being called. Follow the restaurant’s official status.

The server records notifications transactionally with queue changes, deduplicates
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

The ignored database files use 0600. A newly created data directory uses 0700;
existing parent-directory permissions are never changed. Authentication remains in
`state.json`. Back up both the database and state file.

Shop identity is cached and refreshed daily in Tokyo time. Current availability,
groups and official estimates refresh in the background and on manual refresh.
Each collection updates current shop snapshots and appends minute observations for
the history charts. Select `履歴` on a shop to view waiting groups and official
waiting minutes by Japanese calendar date (default: today). Axis times use the
browser timezone, falling back to Tokyo. Missing readings and gaps longer than two
minutes break the line; lower-bound estimates are marked. Refresh reloads stored
data without submitting a queue. Historical recording was paused while the history
UI was retired; those missing intervals cannot be reconstructed from current snapshots.
Raw minute observations are retained for 180 days; older data is retained as
five-minute aggregates, which are not currently displayed in this daily chart.
Historical charts do not calculate predictions or replay automatic reception.
Official-estimate tasks use fresh shop details independently.
Complete catalogs replace membership;
partial/error reads retain known data with stale status. Queue tracking remains
active independently of shop snapshots. Storage runs off the event loop.
Real state, captures, `.env`, `line_client.toml` and `data/` are never committed.

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
