# Task 2 Report: Persistent Personal Queue Tracking

## Result

Implemented durable personal queue tracking across supported merchants. Tracking runs from the
production application lifecycle without requiring an open browser, and browser/test services do
not implicitly start a real coordinator.

## Implementation

- Added SQLite migrations for submission intents, queue sessions, per-minute observations, and
  per-merchant read health.
- Added typed tracking models, a synchronous repository, and an asynchronous minute coordinator.
- Successful manual submissions persist an intent before the POST and resolve it to the returned
  waiting ID afterward. Ambiguous send or post-send persistence failures retain an unresolved
  intent and block duplicate submission across restarts.
- Definite upstream rejection releases the unfinished-intent block. Reconciliation requires the
  same merchant, shop, party, and a ten-minute submission window so an old unresolved attempt does
  not capture an unrelated external ticket.
- Externally observed tickets are adopted with `source=adopted`, unknown `submitted_at`, and no
  invented official-at-submission estimate.
- Successful empty reads mark disappeared active sessions unknown. Failed reads only mark data
  stale. An observed count of zero confirms `called`; terminal sessions are not reopened.
- Cancellation intent is stored before DELETE. Older in-flight observations cannot turn the
  session into call evidence; a later successful read can clear a rejected/stale cancellation
  attempt, while disappearance after the request resolves it as cancelled.
- Added `GET /api/queues` with selected business fields, merchant/shop display names, observations,
  stale state, and no raw upstream payload.
- Homepage and merchant pages now render Japanese tracked queue status, progress, official
  submission estimate, and timestamps. Merchant polling reads persistent `/api/queues` rather
  than issuing browser-driven upstream waiting reads.
- Coordinator read failures are isolated per merchant, persistence failures do not stop later
  merchants, and shutdown cancels a stuck cycle after a bounded timeout.

## Verification

- `UV_CACHE_DIR=/tmp/matoca-uv-cache uv run --no-sync pytest -q`
  - 263 passed, 39 deselected
- `UV_CACHE_DIR=/tmp/matoca-uv-cache uv run --group browser --no-sync pytest -q -m browser tests/browser/test_merchant_console.py tests/browser/test_responsive_layout.py`
  - 17 passed
- `UV_CACHE_DIR=/tmp/matoca-uv-cache uv run --no-sync ruff check src tests`
  - passed
- `UV_CACHE_DIR=/tmp/matoca-uv-cache uv run --no-sync ruff format --check src tests`
  - 81 files already formatted
- `UV_CACHE_DIR=/tmp/matoca-uv-cache uv run --no-sync mypy src`
  - no issues in 40 source files
- `git diff --check`
  - clean

All queue creation/cancellation behavior used offline fakes or intercepted HTTP. No production
credentials, state, upstream mutations, service restarts, or pushes were used.

## Integration Notes

- `MatocaService._operation_lock` remains the common serialization owner for tracked reads and
  upstream mutations. Later automation should call `_create_waiting_unlocked` only while it owns
  this lock; it must not recursively call the public lock-taking method.
- Pending and unresolved intent uniqueness is account-wide. Task 4 can extend the pre-send source
  and state callbacks without replacing the tracking persistence contract.
- Reconciliation uses a ten-minute window. An outage exceeding that window leaves the intent
  unresolved and adopts a subsequently seen queue conservatively; this intentionally prefers
  preventing false training evidence over guessing ownership.
- A transport failure during cancellation remains outcome-unknown and preserves cancellation
  evidence. A later observation of the same ticket after the request clears that evidence; a
  later disappearance resolves cancellation.
