# Core queue console simplification

> Historical change record. Official-estimate automatic reception was subsequently
> restored, followed by raw shop-history recording and charts; see the current README.
> Learned prediction and prediction-derived trend/replay UI remain retired.

Prediction and arrival-time automatic reception have been retired from the running
application. The Japanese console retains merchant selection, available-shop
filtering, search, favorites, official waiting groups/time, immediate reception,
current queue status, cancellation and party defaults in the header settings.

The application no longer starts the automation coordinator or exposes automation,
shop history or trend routes. Prediction/progress estimates are absent from the UI;
prediction-derived notifications are no longer generated. Legacy offline Python
analysis helpers and historical database records remain for compatibility, but are
not invoked by the running console.

Schema 13 seeds one current snapshot per shop from the existing latest observations.
Runtime collection refreshes snapshots every minute without consulting learned
historical windows or appending/pruning historical shop observations. Static shop
identity retains its daily refresh behavior. Queue status tracking remains active.

An explicitly confirmed absence check supports unfinished intents from the retired
automation feature as well as manual reception. It reads every merchant first and
atomically releases the intent and retires its linked task without resubmission.
Called tickets disappearing from the official list leave the current-queue panel;
their call evidence is preserved. Zero groups alone never proves a call.

Validation:

- 440 offline tests passed.
- 44 Chromium browser tests passed using a synthetic local service with external
  traffic blocked, including reception/cancellation and three viewport sizes.
- Desktop and mobile screenshots of the console, reception form and current queue
  were visually inspected.
- Ruff check, Ruff format check and MyPy passed.
- A SQLite backup passed integrity checking before the Supervisor restart.
- After deployment, both merchant consoles returned fresh cached data. Retired
  automation and shop-history routes returned 404; current queues returned 200.

No real queue was created/cancelled and no native refresh was forced during this
change. The state file remains 0600 and shared temporary-directory permissions
remain unchanged. Historical data and backups are ignored by Git.
