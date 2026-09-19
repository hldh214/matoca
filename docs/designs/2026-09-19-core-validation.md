# Core Queue Validation

User approved the sequence: simulation and core availability fixes, actual dining verification when available, snapshot trend evidence, then editable future tasks. Push is not part of this work.

## Simulation and replay

Live simulation is an explicitly labeled task mode. It observes fresh availability, form and account state and records decisions, but never inserts a submission intent or calls queue creation/cancellation. It ends at the first would-submit result. Existing tasks default to live mode; simulation cannot be converted into live implicitly.

Both modes use the same deterministic timing policy. Before arrival, fresh exact official estimates and the existing prediction formula determine timing. From arrival through the existing two-minute grace period, fresh compatible form, availability and no outstanding queue suffice even if an exact estimate is unavailable. After the grace period the task expires. Full-form equality is replaced by compatibility of selected values and required inputs, ignoring irrelevant defaults.

Historical replay examines timestamp-ordered observations and only labels known by each evaluation timestamp. It never submits and does not assume historical account or form validity. Missing or failed observations cannot become an affirmative would-submit result. Results are explicitly timing-only.

## Snapshot evidence

Official estimate revisions are measured over five-minute windows within one shop and a continuously fresh, open, issuable interval. Closed/reset states, gaps and lower bounds are excluded. Statistics use past-only thirty-day history grouped by Tokyo day class/time bucket with explicit fallback scope. Recommendations require enough nonoverlapping intervals and are presented as uncertainty margins, not actual waiting-time labels.

Users may explicitly apply a suggested extra margin to new or editable tasks. Existing real tasks are never changed automatically. Replay compares timing with fixed and suggested margins without inventing actual call accuracy.

## Editing

Unsubmitted tasks can change arrival, party/answers and risk settings after fresh form validation. Merchant, shop and mode are immutable. Expected versions and shared account admission prevent edits from overwriting a task already admitted for submission. Conflicts preserve typed inputs and request review of current state. Historical evaluations remain available.

## Real-world acceptance

At the user's next actual visit: compare original merchant form with this application's selected party/answers; initiate only the queue the user really intends; compare returned number and subsequent groups with the official page; observe the transition to call or unknown disappearance; cancel only if the user actually wants cancellation. Record selected business observations without tokens. Until this occurs, real create-to-call integration and actual-wait calibration remain unverified.
