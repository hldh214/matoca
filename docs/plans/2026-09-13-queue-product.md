# Queue Product Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development task-by-task.

**Goal:** Complete shop comparison, persistent queue tracking, predictions, arrival automation, and browser Push in that order.

**Architecture:** Extend existing service and SQLite with focused analytics, tracking, prediction, automation, and notification modules. All upstream calls continue through existing clients. Single-worker lifecycle owns background work; frontend reads local summaries and uses existing dynamic join forms.

**Tech Stack:** uv Python 3.14, FastAPI, SQLite, vanilla JS, Chromium tests, pywebpush.

**Spec:** docs/designs/2026-09-10-queue-automation-design.md plus the user-approved five-stage scope below. Existing manual console remains the starting point.

## Global Constraints

- All visible Web UI text is Japanese.
- Work on master as explicitly requested. Preserve unrelated changes.
- No real queue creation/cancellation or forced native refresh for tests. No real state or captures in outputs or commits.
- Authentication including VAPID private material remains in state.json with mode 0600; business state in ignored SQLite.
- One process, one worker, Supervisor. No firewall changes.
- Defaults: two adults, zero children; early tolerance 15 minutes; model error 15 minutes.
- No reception-closing prediction. Only explicit user-enabled tasks authorize future automatic submissions.
- Use meaningful offline tests first for protocol/state behavior, isolated browser coverage for new user flows, then pytest/Ruff/format/MyPy/build.
- Existing design controls timing, prediction hierarchy, uncertainty, recovery, and notification behavior. Update obsolete interfaces in old plans to actual current code rather than recreate old UI.

### Task 1: Shop comparison and history

**Files:** create analytics/models.py, analytics/repository.py, web/static/shop-history.js; modify storage/migrations.py, service.py, web/app.py, templates/merchant.html, static/shop-list.js, merchant.js, merchant.css; tests under unit/analytics and browser.

**Interfaces:** service.shop_history(merchant_key, shop_id, day) returns structured observations for a Tokyo calendar date; favorites() / set_favorite(merchant_key, shop_id, enabled) persist per-shop favorites in SQLite. Expose GET /api/merchants/{merchant_key}/shops/{shop_id}/history?day=YYYY-MM-DD and GET /api/favorites, PUT /api/merchants/{merchant_key}/shops/{shop_id}/favorite with enabled boolean.

- [ ] Add offline tests for merchant isolation, UTC day boundaries, missing values, favorite persistence, and sort ordering (unknown estimates last, favorites first).
- [ ] Implement history from current raw observations (no copied stale metrics), including cached name/address/tel/coordinates, empty-day response; expose same-origin mutations and safe Japanese errors.
- [ ] Add Japanese sorting controls, persistent favorite toggles and accessible shop detail dialog with date picker and separate waiting-group and official-minute SVG plots, labeled units/time ticks and gaps for failed samples. Do not load external chart libraries.
- [ ] Verify actual browser sorting/favorites/detail interaction at desktop/mobile sizes without disturbing existing join flow; run relevant gates and commit.

### Task 2: Persistent personal queue tracking

**Files:** create tracking/models.py, repository.py, coordinator.py; modify migrations.py, service.py, web/app.py, dashboard.html, queue-status.js; create tracking UI module and offline tests.

**Interfaces:** persistent queue_sessions and queue_session_observations; GET /api/queues returns cross-merchant session summaries with last observation, progression and terminal status. Tracker reads all supported merchants every minute independent of open browsers; successful create seeds start-time official estimate, manual cancel marks cancelled. Adopt externally-created active tickets without inventing their start time.

- [ ] Test zero groups, unknown disappearance, explicit cancellation, failed reads, restart and minute uniqueness before implementation.
- [ ] Add repository and asynchronous coordinator; share upstream mutation serialization with service and later automation. A missed query cannot mark a queue absent. Only observed zero is a confirmed call; disappearance is unknown.
- [ ] Persist manual submission evidence before returning; keep an identifiable unresolved outcome when post-send persistence fails. Records contain only selected business fields.
- [ ] Render cross-merchant current queue on homepage and progress/timestamps on merchant page. All requests served from tracked state where suitable; no browser-open requirement.
- [ ] Run tracking and browser gates and commit.

### Task 3: Transparent prediction

**Files:** create prediction/models.py, model.py, repository.py; modify tracking and service/console responses plus history/queue UI; tests prediction.

**Interfaces:** predict(merchant, shop, official_minutes, at) -> fast_minutes, typical_minutes, confidence, effective_samples, level; optionally None if no fresh official estimate. Confirmed sessions generate one training sample each.

- [ ] Test weighted quantiles, 30-day half-life, shrinkage n/(n+10), hierarchy (shop/day-class/3h bucket, shop, merchant, cold-start), cancellation/unknown exclusions and null estimates.
- [ ] Implement pure model and repository over confirmed start-to-zero sessions; low/medium/high confidence boundaries 5 and 20 effective samples. Cold start ratio 1, explicitly low confidence.
- [ ] Display predicted range separately from official estimate, confidence and sample information. Active-ticket trajectory may estimate remaining time only with decreasing observed groups; avoid claiming a call occurred on disappearance.
- [ ] Verify UI/API and commit.

### Task 4: One-time arrival tasks

**Files:** create automation/models.py, repository.py, runner.py, coordinator.py; web automation routes and static/automation.js; extend existing join form and dialogs; tests automation/browser.

**Interfaces:** /api/automation/tasks GET/POST and /api/automation/tasks/{id} DELETE cancels only an unsubmitted task; persistent transitions and events. Inputs include merchant, shop, arrival timestamp with offset, timezone, party/answers and 15-minute defaults.

- [ ] Test timing now+max(0,fast-margin)>=arrival-tolerance, deadline expiry, fresh live form validation, account-wide existing queue conflict and concurrent manual submit, write-before-POST, recovery of submitting and ambiguous POST without replay.
- [ ] Implement durable state machine with short SQLite transactions; share account mutation lock; fresh form and waiting verification immediately before POST. Never hold DB transactions across network calls. Persist submitting before send and reconcile outcomes. Restart cannot replay uncertain writes.
- [ ] Add single-screen Japanese arrival form using current dynamic count/confirmation controls, explicit eventual-submit consent, task status/decision and cancel-monitoring control. Prevent nonfuture/naive times and stale-data submission.
- [ ] Wire lifecycle, minute collection for enabled tasks, test synthetic execution/restart plus browser flow; commit.

### Task 5: Browser Web Push and delivery

**Files:** notifications keys/models/repository/sender/dispatcher/events; state model/store; storage migration; PWA manifest/service worker/icons/push.js; routes, templates and README; tests notifications/browser.

**Interfaces:** GET /api/push/public-key, POST/DELETE /api/push/subscriptions, POST /api/push/test, GET local notification history. VAPID generated atomically in state.json on explicit enable. Event outbox is durable and per-subscription deliveries retry 1/5/15 minutes, invalid 404/410 subscriptions removed.

- [ ] Test secret-safe state migration, deduplication, targeted test notifications, expired endpoints, transient delivery, transactional domain-event publication and payload redaction.
- [ ] Use maintained pywebpush library; verify its actual primary documentation and API. Validate HTTPS browser-vendor endpoints and keys; redact capability material everywhere. Send off event loop with bounded timeout.
- [ ] Add manifest and self-contained icons, root-scope service worker with safe same-origin notification navigation and no API caching. Japanese enable/test/disable controls under header settings; explicit permission gesture, HTTPS capability and iOS home-screen guidance.
- [ ] Wire automated submission result, task attention/failure/expiry, groups thresholds 10/5/0 and predicted call moving >=10 minutes earlier to deduplicated notifications. Push errors never roll back queue transitions.
- [ ] Run full offline/browser/Ruff/format/MyPy/build, review complete integration, update README to actual implementation, push and deploy through Supervisor. Physical browser permission/delivery remains user initiated; provide URL and instructions.
