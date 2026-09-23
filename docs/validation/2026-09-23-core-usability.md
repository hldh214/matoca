# Core queue usability

The merchant console now shows the account's current ticket across merchants.
Cancellation captures and checks both the issuing merchant and waiting ID before
calling the corresponding endpoint and removing the local ticket. Existing queue
mutation/reload revision guards remain in use.

Manual refresh reloads the latest tracked queue alongside the shop console. Home
and merchant pages preserve the last ticket on failed reads and offer retry. Legacy
automation intents can use the existing confirmed-absence resolution workflow.

The Japanese ticket adds party size, reception time, a map-search link and distinct
pre-call/calling/pending states. Cancellation remains a secondary, confirmed action.
Success and favorite-save failure feedback is explicit. The merchant toolbar has a
favorites filter and remembers filtering, search and sorting per merchant in the
browser; unavailable browser storage does not prevent using the console.

Validation used only synthetic tickets and blocked external browser requests:

- 443 offline tests and 50 Chromium browser tests passed.
- Cross-merchant cancellation, legacy-intent resolution, refresh/retry, malformed
  queue responses, favorite-save errors and view persistence were exercised.
- Desktop, 390px mobile and 320px narrow layouts were inspected; long merchant
  names remain visible and the toolbar fits without horizontal overflow.
- Ruff check, Ruff format check and MyPy passed.

No real ticket was created/cancelled and no credential refresh was forced.
