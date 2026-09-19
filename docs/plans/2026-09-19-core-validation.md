# Core Queue Validation Implementation Plan

> Execute with superpowers:subagent-driven-development, sequential implementation and scoped reviews.

**Goal:** Make automatic queue decisions observable without submitting, improve core decision availability, use collected trends honestly, and support task edits.

**Architecture:** Extend existing automation records and SQLite with evaluation evidence. Reuse one pure timing decision for live simulation and historical replay; real submission keeps existing live checks and durable intents. Snapshot trends remain distinct from actual wait labels.

**Spec:** [Core Queue Validation](../designs/2026-09-19-core-validation.md), reflecting user-approved core priorities in the September19 conversation: order1,4,2,3,5. Actual dining validation requires user participation; never create a real queue for development.

## Global constraints

- Master checkout, preserve changes. uv-managed Python. Japanese UI, merchant terminology.
- No real queue creation/cancellation, forced native refresh, or Push work for this development.
- state.json0600 remains auth-only; additive SQLite migrations, ignored business data.
- Single Supervisor worker; storage off-loop; shared operation admission for edits/cancel/submit.
- Existing real tasks retain behavior unless an explicitly documented core correction applies.
- Full verification with PYTHONPATH unset; browser explicit timezone. No production static/backend mismatch: stage frontend assets outside served tree until integration or deploy coherent releases.

## Task 1: Simulation and core availability

Files: automation models/repository/runner plus new decisions.py and replay.py; migration; service/routes; automation/join UI; focused automation/browser tests.

- [x] Add task mode live/simulation, old records default live. Simulation uses read-only live shop/account checks and persists structured decisions without calling submission core or inserting queue_intents. Never allow mode conversion into live without a new explicit live task consent.
- [x] Persist evaluation timestamp, freshness, official exact/lower-bound estimate, prediction, reason code, would-submit, arrival and thresholds. Expose selected per-task history in Japanese UI. A simulation records first would-submit and ends as simulated, never queued; it may be cancelled before that.
- [x] Extract deterministic decision function. Before arrival require fresh exact estimate and existing timing formula; at arrival through existing2-minute grace use fresh availability/account/form checks without exact prediction. Past grace expires. Tests: missing/lower-bound before vs after arrival, freshness and late deadline.
- [x] Replace whole-form equality with relevant semantics: validate selected counts against live bounds; disregard defaults and irrelevant extra metadata; preserve required enabled fields and selected confirmation title/index/text meanings. Changed meaning or unsupported new required input needs_attention. Support signatures already stored by comparing structured old/new forms, not changing all existing tasks unnecessarily.
- [x] Add read-only history replay endpoint/UI taking merchant/shop, arrival with offset and explicit day; evaluate ordered stored observations only through each timestamp, use past-only prediction labels, stale/error gaps never imply successful submission. Explain historical forms/account availability unknown, results timing-only; never POST or mutate real tasks. No future lookahead.
- [x] TDD focused deterministic/storage/API/browser tests, then full gates. Document actual dining verification checklist without asking for real action now.

## Task 2: Snapshot trend evidence

Files: prediction/trends.py, analytics models/repository, service and shop-history UI; tests.

- [x] Analyze exact fresh within-shop estimate pairs with5-minute horizon (tolerance1min); never pair across missing/error/lower-bound intervals or large gaps. Summarize sample count and downward changes by shop/day class/Tokyo3h bucket, fallback merchant explicitly labeled; last30days as-of limit.
- [x] Show recent official trend and historical drop statistics separate from learned actual waits. Calculate suggested extra uncertainty from upper quantile downward revisions; display suggestion rather than silently changing saved real-task risk preferences. User can apply suggestion explicitly to future/edit task. Sparse data remains explicitly insufficient; no closing-time prediction.
- [x] Replay includes fixed-margin vs trend-suggested-margin timing comparison; cannot claim actual call accuracy without real labels. Tests time boundaries/no leakage/gaps and UI.

## Task 3: Edit unsubmitted tasks

Files: automation repository/runner/service/routes, existing dialog/panel; tests.

- [x] PUT task with expected_version and updated arrival/party/answers/risk values. Immutable merchant/shop/mode; scheduled/monitoring or attention-without-intent only. Fresh form validation; CAS under same admission lock as submission/cancel, no lost updates.
- [x] Future arrival required; explicit live consent retained/reconfirmed in edit dialog. Recompute signature and next evaluation, wake coordinator; append history. Cancel/edit races against submitting return clear409, never alter submitted task.
- [x] Prefill existing single-screen Japanese dialog; show reason/next evaluation and detailed decision evidence. Preserve simulation label on edits.
- [x] Focused tests then full pytest/Ruff/format/MyPy/browser/build; scoped review then complete integration review. Push/deploy coherent final version after backup. Real dining validation remains explicitly unperformed.
