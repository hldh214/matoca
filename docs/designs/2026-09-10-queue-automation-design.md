# Matoca Queue Automation and Web Console Design

Date: 2026-09-10

## Purpose

Matoca is a personal, single-process service that monitors supported restaurant merchants,
helps the user compare every shop quickly, and can join a queue at a calculated time so that
the call is likely to align with the user's planned arrival.

The first release of queue automation supports one-time arrival plans. It learns from minute
snapshots and confirmed queue journeys without claiming that the upstream estimate is exact.
All visible Web UI text is Japanese.

## Confirmed Product Decisions

- The merchant selector remains the home page. Supported merchants are built into the package.
- The merchant page defaults to shops that are currently accepting queues.
- Shop rows show current waiting groups, the Matoca estimate, the local prediction, and actions.
- Static shop metadata is cached and refreshed daily.
- Operational observations and automation state use SQLite. Authentication state remains in
  `state.json`.
- Raw minute observations are retained for 180 days. Older observations are reduced to
  five-minute aggregates and retained.
- The default party preference is two adults and zero children.
- The default early-call tolerance is 15 minutes. The default model error margin is 15 minutes.
- A user enables a one-time automation task explicitly. Enabling it authorizes the eventual
  queue submission without another confirmation dialog.
- The system does not predict when queue reception will close and does not submit early to beat
  a predicted closing time.
- The cold-start prediction uses the current Matoca estimate with low confidence.
- Browser Push is the first external notification mechanism. No webhook is included.

## Scope

### Included

- A cached, responsive merchant and shop console.
- Minute-level shop observations during the effective business window.
- One-time automated queue tasks based on a target arrival time.
- Transparent, hierarchical prediction with confidence information.
- Active queue tracking and confirmed training samples.
- Dynamic queue forms for the currently supported merchants.
- Web Push subscriptions and task/queue notifications.
- Restart recovery and ambiguous-submission reconciliation.

### Not Included

- Recurring automation tasks.
- Predicting the end of queue reception.
- PostgreSQL or a multi-worker deployment.
- A machine-learning framework or opaque trained model.
- LINE Messaging API notifications or generic webhooks.
- Automatic replay of an ambiguous queue submission.
- Support for a new merchant without inspecting its real form and behavior first.

## Runtime Architecture

Supervisor continues to run one FastAPI process with one worker. FastAPI lifespan owns two
coordinators:

1. The collection coordinator refreshes catalogs and shop observations.
2. The automation coordinator evaluates tasks, submits eligible queues, tracks active tickets,
   and dispatches notification events.

The existing LINE and Matoca clients remain the only upstream protocol layer. Coordinators call
application services rather than constructing HTTP or Thrift requests themselves.

The main component boundaries are:

- `catalog`: immutable packaged merchant definitions.
- `state`: LINE and LIFF credentials in `state.json`.
- `storage`: SQLite migrations and typed repositories.
- `collection`: adaptive polling and catalog refresh.
- `prediction`: pure prediction and calibration logic.
- `automation`: task state machine and submission reconciliation.
- `notifications`: Web Push subscriptions and delivery.
- `web`: Japanese pages and local JSON endpoints.

Failure in one merchant collection does not stop collection for another merchant. Notification
failure does not alter a queue task's result.

## Persistent Storage

The SQLite database is a local ignored file under the project directory. The runtime data
directory is mode `0700`; the database and its WAL/SHM sidecars are enforced as mode `0600`.
Failure to establish those permissions prevents the storage-backed coordinators from starting.

SQLite uses WAL mode, a busy timeout, foreign keys, short transactions, and batch inserts. Schema
updates are incremental package migrations tracked through `PRAGMA user_version`; no ORM or
external migration service is introduced.

The principal tables are:

- `shops`: cached merchant/shop identity, address, coordinates, image, and last refresh time.
- `shop_observations`: one structured row per merchant, shop, and observed minute.
- `shop_observation_rollups_5m`: five-minute summaries used after raw retention expires.
- `automation_tasks`: one-time arrival plans, form values, risk settings, and current state.
- `automation_events`: append-only task decisions and failure/recovery reasons.
- `queue_sessions`: successful or reconciled queue submissions and their terminal result.
- `queue_session_observations`: timestamped groups-ahead and upstream estimate for active tickets.
- `prediction_samples`: confirmed actual-wait samples derived from queue journeys.
- `prediction_stats`: reproducible materialized statistics for fast evaluation.
- `preferences`: global party and risk defaults.
- `push_subscriptions`: browser Push endpoint and encryption material.
- `notification_events`: deduplicated notification outbox and delivery result.

The unique key for raw observations is `(merchant_key, shop_id, observed_minute)`. Restarting or
repeating a collection cycle therefore cannot duplicate the same minute.

Tokens never enter SQLite. Push endpoints and subscription keys are sensitive capability data:
they are never printed or logged. VAPID public/private keys are generated once and stored under a
Web Push section in the permission-protected `state.json`, because the private key is application
authentication material.

All stored timestamps are UTC. The browser renders its own time zone when available and falls
back to Asia/Tokyo (UTC+9).

## Collection Strategy

For a newly supported merchant without history, the service polls the shop list every five
minutes throughout the day. Once observations exist, it derives a broad effective window from
the earliest observed open time and latest observed close time in the preceding 30 days:

- Poll once per minute from 30 minutes before the earliest opening through 30 minutes after the
  latest closing.
- Poll once per 15 minutes outside that window.
- Poll once per minute during the relevant period of an enabled automation task.
- Skip a cycle if the preceding request for the same merchant is still running. Do not stack
  upstream requests.

This window only controls collection frequency. It is not a reception-closing prediction and is
never an input that causes an early queue submission.

The captured API shape requires two read layers. The paginated shop-list endpoint supplies shop
identity, coordinates, and `current_waiting`, but it does not supply `waiting_time`, `is_issuable`,
`is_open`, or the live form. Each minute cycle therefore fetches the merchant's shop-list pages
and then fetches one detail response per shop with concurrency limited to four. For Sawayaka this
is approximately 35 read requests per minute during the effective window; La Ohana Yokohama
Honmoku currently needs approximately two.

The collector merges the list and detail results, then inserts the complete cycle in one short
transaction. A failed detail stores null for fields that were not freshly observed and records a
per-shop detail error; it does not copy an old estimate into a new observation. The cached shop
record remains available to the UI with its own last-detail timestamp and stale label. HTTP 429
honors `Retry-After` when present and otherwise applies bounded exponential backoff for that
merchant. A backoff or partial cycle cannot trigger automated submission from stale detail data.

Static catalog fields are refreshed at most once per day. Queue forms are not trusted as daily
cache: shop detail is fetched when a manual form opens and again immediately before an automated
submission.

A daily maintenance transaction creates five-minute rollups for raw observations older than 180
days. Raw rows are deleted only after the corresponding rollups commit successfully.

## Prediction Model

The primary output answers one question: if the user joins this shop now, when might the user be
called? It returns:

- a fast-case wait duration;
- a typical wait duration;
- a confidence level and the fallback level used.

The model is transparent and hierarchical. A confirmed queue session produces a ratio between
actual wait and the Matoca estimate at submission. Samples receive exponential recency weighting
with a 30-day half-life. At each hierarchy level the model calculates a weighted 20th percentile
for the fast case and a weighted median for the typical case.

The hierarchy is:

1. shop, weekday/weekend class, and three-hour local-time bucket;
2. shop;
3. merchant;
4. cold-start ratio `1.0`, meaning the current Matoca estimate.

Each local result is shrunk toward its parent using `weight = effective_sample_count /
(effective_sample_count + 10)`. This allows learning from the first valid samples without treating
a small sample as authoritative. Confidence is low below 5 effective samples, medium from 5 to
19, and high from 20 onward at the selected local level.

Minute snapshots supply current input, trend display, volatility, and data freshness. They do not
pretend to be actual wait labels because queue arrivals and calls occur simultaneously. The first
version does not extrapolate a reception-closing time or future queue arrivals.

A queue session becomes a confirmed training sample only when the service observes its
groups-ahead value reach zero. A queue canceled through this service is excluded. A queue that
disappears without an observed zero is marked unknown and excluded because it could have been
called, canceled elsewhere, or automatically canceled after a missed call.

After submission, the active-ticket estimate additionally uses the observed groups-ahead
trajectory. This improves the live call prediction but does not rewrite the pre-submission sample
until the session reaches a confirmed terminal state.

## Automated Submission Decision

Each task stores:

- merchant and shop;
- target arrival in UTC and the originating browser time zone;
- adult and child counts;
- resolved form answers;
- early-call tolerance, default 15 minutes;
- model error margin, default 15 minutes;
- creation time, state, version, and last decision.

For current time `now`, arrival `A`, early tolerance `E`, fast predicted wait `W`, and model error
margin `M`, the conservative earliest-call estimate is:

```text
now + max(0, W - M)
```

The task submits when that estimate is no earlier than `A - E`, provided the shop is currently
issuable and all live validation succeeds. A longer predicted wait therefore permits an earlier
submission; a suddenly falling estimate delays submission. The decision is recalculated from the
new snapshot every minute.

At arrival time the timing condition is necessarily satisfied. If the shop is still issuable and
validation succeeds, the task submits at the next evaluation. If the shop is not issuable at the
arrival deadline, the task expires without guessing that reception might reopen.

Multiple future tasks may be saved, but only one task may be in submission or active-queue
tracking for the LINE account. Before every POST the service checks current waiting state. An
existing queue blocks submission and moves the task to `needs_attention` rather than canceling or
replacing the existing queue.

Task states are:

```text
scheduled -> monitoring -> submitting -> queued -> completed
                                |            |
                                |            +-> cancelled / unknown
                                +-> reconciling -> queued / needs_attention

scheduled / monitoring -> cancelled / expired / failed / needs_attention
```

## Submission Safety and Recovery

Immediately before POSTing, the service fetches live shop detail, live current-waiting state, and
validates the task against the current form. It persists `submitting` before sending the request.

An explicit upstream rejection may be retried only while the task remains before its arrival
deadline and only when the rejection is classified as transient. Authentication still follows the
existing single LIFF-token reissue and one retry for 401/403.

When the request outcome is ambiguous, such as a disconnect after the body was sent, the service
never repeats the POST directly. It enters `reconciling` and queries current waiting. A matching
queue becomes `queued`; a different existing queue becomes `needs_attention`. If no queue can be
confirmed, the result remains `needs_attention` and requires the user to resolve it. Startup uses
the same reconciliation for persisted `submitting` or `reconciling` tasks.

Real queue creation, cancellation, or forced Native Refresh is never performed merely for a test.
A live destructive integration check still requires immediate explicit user confirmation.

## Dynamic Queue Forms

Global preferences prefill two adults and zero children. Counts are clamped to the live minimum
and maximum.

- If `is_confirm_child` is false, the child control is hidden and zero is submitted.
- If it is true, the child control is displayed with its live default and limits.
- Enabled confirmation items with one unambiguous option are selected automatically.
- The Sawayaka missed-call cancellation confirmation is selected by default.
- La Ohana Yokohama Honmoku displays adult and child counts and has no enabled confirmation item
  in the observed form.
- Telephone and advance-information controls are displayed only when the live form enables them.

If a newly supported merchant has multiple meaningful choices, its adapter must define the
default only after the original HTML and live form are inspected. Until then, an automation task
requiring those choices cannot be enabled. This prevents a generic first-option rule from silently
choosing seating, allergy, or other business preferences.

An enabled task stores the resolved answers, but submission validates them against a freshly
fetched form. An incompatible change moves the task to `needs_attention`.

## Web UI

The home page is the merchant selector. It currently presents Sawayaka and La Ohana Yokohama
Honmoku. The La Ohana label remains the full merchant-specific name because its LIFF supports that
single shop.

The merchant page uses one universal layout rather than merchant-specific region navigation:

- The header contains the merchant name, the next automation summary, and
  `自動受付を設定`.
- A persistent status band directly below the header shows either the current automation task or
  the current queue.
- The default filter is `受付可能`; `すべて` and text search remain available.
- Each dense shop row shows identity/address, reception state, waiting groups, typical and
  fast-case prediction, and an action.
- The same rows become stacked, full-width controls on narrow screens.
- Every live value includes a visible last-updated time. Stale cached data is labeled as stale and
  is never presented as current.

The automation dialog is a single screen, not a wizard. It contains shop, target date/time,
dynamic party fields, optional detailed risk settings, current prediction, enabled confirmation
items, and one activation button. Enabling states clearly that submission occurs without another
confirmation when conditions are met.

Before submission, the status band shows target shop, arrival, current decision, and next
evaluation. After submission it shows queue number, groups ahead, predicted call time, target
arrival, progress, cancellation, and last update.

All UI copy, empty states, validation messages, notifications, and manifest metadata are Japanese.

## Web Push

The application provides a PWA manifest, application icons, and a Service Worker. Android and
desktop users enable notifications through an explicit `通知を有効にする` action. The action must
be a user gesture; permission is never requested automatically on page load.

iOS users visiting a normal browser tab see Japanese instructions to add the app to the Home
Screen. Push enablement is offered from the installed Home Screen app where supported.

The backend exposes the VAPID public key and subscription create/delete endpoints. It sends
encrypted notifications using a maintained Web Push protocol library rather than implementing
payload encryption manually.

Notification events are generated for:

- automated submission success;
- task failure, expiry, or `needs_attention`;
- active queue groups-ahead crossing 10, 5, and 0;
- a materially changed predicted call time when it moves earlier by at least 10 minutes.

Each event has a stable deduplication key. Delivery failure does not affect the originating task.
Transient failures receive bounded retries; HTTP 404 or 410 removes the invalid subscription.
Notification payloads contain merchant/shop display names, non-secret queue status, and a relative
application path. They never contain LINE tokens, Push subscription keys, LINE account IDs, adid,
or full upstream response bodies.

The UI includes `テスト通知` and `通知を解除`. Notification clicks navigate to the relevant
merchant page; an expired Cloudflare Zero Trust session may require login before the page opens.

## Local API Surface

Existing merchant, shop, queue-create, and queue-cancel routes remain compatible. New route groups
provide:

- latest cached shop state and prediction;
- preferences read/update;
- automation task list/create/update/cancel;
- queue and automation event history;
- VAPID public key and Push subscription create/delete/test.

Mutating routes continue to use the existing same-origin request checks. API errors expose stable
Japanese user messages and internal error codes without leaking credentials or upstream bodies.

## Failure Behavior

- Collection failure preserves the last snapshot and records its age and error category.
- Stale observations cannot trigger an automated submission.
- One merchant's failure does not stop another merchant's cycle.
- Database write failure prevents task state advancement and therefore prevents the related POST.
- A post-send database failure is treated as ambiguous and reconciled before any further action.
- Form incompatibility and an existing queue produce `needs_attention`.
- Notification delivery failure remains in the notification outbox and does not roll back queue
  state.
- Unsupported Push browsers retain complete in-app event history.

## Verification Strategy

Verification focuses on behavior that could create a wrong or duplicate queue rather than broad
coverage targets:

- SQLite migration, uniqueness, batch insertion, retention, and restart recovery.
- Dynamic Sawayaka and La Ohana form rendering/defaults.
- Prediction hierarchy, cold start, timing threshold, and stale-data rejection.
- Task state transitions, current-waiting conflict, and ambiguous POST reconciliation.
- Web Push subscribe/unsubscribe, deduplication, and expired-subscription cleanup with outbound
  Push requests mocked.
- Japanese page rendering and responsive Playwright checks for the merchant console, automation
  dialog, monitoring state, and queued state.
- Existing pytest, Ruff check, Ruff format check, MyPy, package build, and static JavaScript parse
  checks.

Automated tests intercept every Matoca POST and DELETE. Live validation is read-only unless the
user separately authorizes a specific real queue creation or cancellation immediately beforehand.

## Operational Constraints

- Supervisor remains the only process supervisor.
- The HTTP service remains on the configured high port and existing LAN bind address.
- No systemd unit, UFW rule, nftables rule, database daemon, or additional application
  authentication is introduced.
- Cloudflare Zero Trust remains responsible for public access control.
- The design assumes one process and one worker. SQLite and the in-process coordinator do not
  imply multi-worker safety.
