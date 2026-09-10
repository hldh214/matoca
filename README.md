# Matoca Service Design

Date: 2026-09-10

## Goal

Build a single-user service for a Linux VPS that:

- Bootstraps from one LINE native access-token and refresh-token pair.
- Rotates and persists both tokens automatically.
- Obtains merchant-specific LIFF access tokens.
- Uses LIFF access tokens to call Matoca APIs.
- Supports shop discovery, shop details, current waiting status, joining a
  queue, and later cancellation.
- Supports Sawayaka first while keeping merchant-specific values in
  configuration.
- Exposes a small Web UI through Cloudflare Zero Trust.

The service cannot promise permanent operation. LINE logout, device
revocation, account restrictions, token revocation, or private protocol
changes can require a new bootstrap.

## Deployment Boundary

The application runs as one process on a Linux VPS and binds only to
`127.0.0.1`. A `cloudflared` tunnel is the only intended inbound path.
Cloudflare Zero Trust performs user authentication.

The application still performs CSRF and Origin validation for state-changing
requests. It never displays or returns raw LINE or LIFF tokens.

## Technology

- Python 3.10 initially, matching the VPS. Code targets Python 3.10 or newer.
- FastAPI and Uvicorn with one worker.
- HTTPX for LINE and Matoca HTTP calls.
- Apache Thrift Compact Protocol primitives for the private LINE RPC payloads.
- Pydantic for configuration and domain models.
- Jinja2 and HTMX for the initial Web UI.
- pytest and respx for protocol and HTTP regression tests.
- `fcntl.flock` plus atomic file replacement for persistent state.

No database is required for the first version.

## Files and State

Fixed configuration:

```text
/etc/matoca/config.toml
```

Bootstrap secrets:

```text
/etc/matoca/bootstrap.env
```

Mutable state:

```text
/var/lib/matoca/state.json
/var/lib/matoca/state.lock
/var/lib/matoca/events.jsonl
```

Secret and state files must be owned by the service user and have mode `0600`.

The bootstrap environment contains both tokens because the captured refresh
request uses the old native access token in `x-line-access` and the refresh
token in the Thrift request body.

After the first successful import, rotated credentials are read from
`state.json`; the original environment values are not used again.

## Token State

`state.json` contains:

```json
{
  "version": 1,
  "line": {
    "access_token": "...",
    "refresh_token": "...",
    "access_expires_at": "...",
    "refresh_expires_at": "...",
    "rtid": "...",
    "updated_at": "..."
  },
  "liff_tokens": {}
}
```

Updates use this sequence:

1. Acquire an exclusive file lock.
2. Reload the newest state.
3. Perform at most one token refresh.
4. Validate and decode the returned token pair.
5. Write the complete new state to a temporary file.
6. Flush and `fsync` the temporary file.
7. Atomically replace `state.json`.
8. Release the lock.

The new token pair is persisted before any subsequent LIFF or Matoca request.
Ambiguous network failures during refresh are not retried automatically,
because the old refresh token may already have been consumed.

## Captured LINE Flow

Native token refresh:

```text
POST https://legy-jp.line-apps.com/EXT/auth/tokenrefresh/v1
Content-Type: application/x-thrift
Thrift method: refresh
Header: x-line-access: <old native access token>
Body: <old native refresh token>
Response: <new native access token> + <new native refresh token>
```

Observed properties:

- Native access token lifetime is seven days.
- Refresh-token expiry rolls forward approximately one year.
- The refresh-token JWT changes on refresh.
- Its `ati` claim changes to the new access-token `jti`.
- Its `rot` claim is `ROTATE`.

The response is followed by a
`reportRefreshedAccessToken` RPC. Reporting failure is logged but does not
replace the already-persisted token pair.

LIFF view issuance:

```text
POST https://legy-jp.line-apps.com/LIFF1
Content-Type: application/x-thrift
Thrift method: issueLiffView
Header: x-line-access: <current native access token>
Header: x-line-liff-id: <merchant LIFF ID>
Response: LIFF access token and related LIFF view data
```

LIFF tokens are cached per LIFF ID. If a Matoca request rejects a LIFF token,
the service obtains one new LIFF token and retries the idempotent request
once. State-changing queue requests are never blindly retried.

## Matoca API

All authenticated Matoca requests use:

```http
Authorization: Bearer <LIFF access token>
X-Access-Type: mini
Accept: application/json
Origin: <merchant origin>
```

Known endpoints:

```text
POST   /liff/auth
GET    /liff/shops
GET    /liff/shops/{shop_id}
GET    /liff/waiting
POST   /liff/waiting
```

Captured queue creation payload:

```json
{
  "shop_id": "3272",
  "adult_count": 2,
  "child_count": 0,
  "answer1": 0,
  "answer2": null,
  "lat": 34.7042983,
  "lng": 137.7344733,
  "in_advance_information": "",
  "ref": "web"
}
```

The form is generated dynamically from the `forms` object returned by shop
details. Adult/child limits and confirmation answers are never hard-coded for
Sawayaka.

The Web UI uses browser geolocation for `lat` and `lng`, with an explicit
manual fallback. It must not silently substitute the VPS location.

## Missing Captures

Implementation can start without these, but the corresponding features remain
disabled until actual traffic confirms them:

- Active-queue response from `GET /liff/waiting`.
- Detail response from `GET /liff/waiting/{waiting_id}`.
- Actual cancellation request, expected to be
  `DELETE /liff/waiting/{waiting_id}` based on the JavaScript bundle.
- Response and subsequent status after cancellation.
- Duplicate queue, suspended shop, invalid form, expired LIFF token, invalid
  refresh token, rate-limit, and server-error responses.

## Merchant Model

Merchant configuration is data, not a subclass:

```toml
[merchants.sawayaka]
name = "Sawayaka"
liff_id = "2006055787-m6P6OJ38"
api_base_url = "https://admin.junbanmachi.jp"
origin = "https://exclusive-mini.junbanmachi.jp"
entry_url = "https://exclusive-mini.junbanmachi.jp/sawayaka/"
```

Each LIFF ID has its own cached LIFF access token. The generic Matoca client
receives a merchant configuration and implements the common endpoints.

## Web UI

Initial pages:

- System/token health without raw token values.
- Merchant selector.
- Nearby and keyword shop search.
- Shop details and dynamic queue form.
- Current waiting status.
- Queue creation confirmation and result.
- Cancellation only after its request is captured and tested.

Queue creation is protected against accidental duplication by:

- A current-waiting preflight check.
- A process-local operation lock.
- A disabled submit button while the request is running.
- No automatic retry after an ambiguous POST failure.

## Modules

```text
src/matoca_service/
  main.py
  config.py
  line/
    thrift_codec.py
    token_manager.py
    refresh.py
    liff.py
  matoca/
    client.py
    models.py
    service.py
  merchants/
    config.py
    registry.py
  state/
    models.py
    store.py
    locking.py
  web/
    routes.py
    templates/
    static/
  jobs/
    waiting_poll.py
```

## Testing

- Golden-byte tests built from sanitized captured Thrift messages.
- JWT claim and expiry tests.
- Atomic state replacement and lock-contention tests.
- Refresh rotation tests confirming both returned tokens are persisted.
- HTTP tests using recorded, sanitized Matoca responses.
- Tests proving POST/DELETE requests are not retried automatically.
- Merchant configuration tests proving no Sawayaka-specific API logic leaks
  into the generic client.

## Delivery Phases

1. Project foundation, configuration, state storage, logging, and tests.
2. Thrift codec and native token refresh.
3. LIFF issuance and per-merchant LIFF token cache.
4. Matoca shop and current-waiting read operations.
5. Queue creation and basic Web UI.
6. Cancellation and full waiting-state display after missing captures are
   obtained.
