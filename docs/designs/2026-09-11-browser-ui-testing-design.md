# Browser UI Testing Design

## Goal

Add deterministic browser testing for the Japanese Matoca Web UI without using the
running Supervisor service, real credentials, or real Matoca queue mutations. Browser
tests are a separate quality gate and do not run as part of the default unit test suite.

The first version verifies behavior and geometry rather than comparing committed pixel
baselines. Failed tests retain screenshots and Playwright traces for diagnosis.

## Root Cause Of The Previous Failure

The installed Playwright and matching Chromium build work on the target Linux host. A
minimal offline page completes and captures a screenshot when run outside the restricted
agent sandbox. The previous timeout was caused by sandbox restrictions on browser
processes and local socket operations, not by missing Chromium or application code.

Browser tests therefore run normally on developer hosts and CI. An agent environment
with equivalent process restrictions must run this separate gate outside that sandbox.

## Chosen Architecture

Use Playwright for Python through its pytest plugin. This keeps dependency management in
`uv` and avoids introducing a separate Node project solely for tests.

Add a dedicated `browser` dependency group containing Playwright and its pytest plugin.
The Playwright version owns the matching Chromium binary. Installation is explicit:

```bash
uv run --group browser python -m playwright install --with-deps chromium
```

Mark every browser test with `browser`. The default pytest configuration excludes that
marker. The standalone browser gate overrides the selection:

```bash
uv run --group browser pytest -m browser \
  --tracing retain-on-failure \
  --screenshot only-on-failure \
  --full-page-screenshot
```

Artifacts go to `test-results/`, which is ignored by Git.

## Isolated Test Application

Browser tests start `create_app()` with a purpose-built in-memory service on an unused
`127.0.0.1` port. The server uses the real FastAPI routes, Jinja templates, CSS, and ES
modules. It does not start the collection coordinator and never reads `state.json`.

The fake service exposes deterministic examples for:

- both supported merchants;
- available, closed, stopped, and stale shops;
- live shop detail, party limits, and confirmation choices;
- global party preferences;
- empty and active current-waiting states;
- successful fake queue creation and cancellation.

State-changing calls only update the fake service's in-memory state. Tests can inspect
recorded submissions to verify payloads without sending them upstream.

The server fixture binds a socket first, passes that socket to Uvicorn, waits on a
condition rather than sleeping for a fixed duration, and always shuts the server down.
It must report startup and shutdown failures clearly.

## Network Safety

The browser context rejects requests whose destination is not the temporary loopback
server. Merchant images used by tests are local or embedded fixtures. Any attempted
external request fails the test.

The fake service is the primary safety boundary. Request interception is defense in
depth, not a substitute for dependency injection. No test imports production runtime
settings, authenticates with LINE, wakes collection, or reaches the Supervisor process.

Tests also fail on unexpected page errors and browser console errors. Neither response
bodies nor credentials are logged.

## Browser Coverage

Run Chromium headlessly at these viewport sizes:

- desktop: `1440x900`;
- common mobile: `390x844`;
- narrow mobile: `320x568`.

The suite covers the following user-visible behavior:

1. The Japanese merchant selector renders both full merchant names and navigates to a
   merchant console.
2. The console defaults to `受付可能`, shows accurate available/total counts, switches
   to all shops, and filters by search text.
3. Shop rows display server-decided status, waiting groups, and `公式目安`.
4. Header settings load and persist party defaults; a saved change applies only to the
   next join dialog.
5. The join dialog loads live limits, clamps steppers, displays supported confirmation
   choices, and submits the selected values to the fake service.
6. A successful fake join updates the current queue band and disables other join actions.
7. The cancel dialog cancels only the fake queue and restores join actions.
8. Desktop and mobile pages have no horizontal overflow. Key regions remain within the
   viewport, do not overlap incoherently, and the mobile queue region keeps a stable
   height as its state changes.

Existing synthetic DOM tests continue to cover detailed response races and malformed
form states. Browser tests focus on real browser integration and visible workflows rather
than duplicating every state-machine permutation.

## Visual Verification

The first version uses DOM geometry and computed-style assertions instead of committed
golden screenshots. This avoids false failures caused by differences in fonts, raster
rendering, or Chromium patch versions.

Screenshots and traces are retained only on failure. They provide enough evidence to
inspect the rendered page, action history, DOM snapshots, console output, and requests.
Pixel baselines may be added later after CI images, browser builds, and fonts are pinned.

## Continuous Integration

Add a dedicated GitHub Actions workflow for the browser gate. It installs the uv-managed
Python environment, installs the matching Chromium build and Linux dependencies, then
runs only `-m browser` with failure artifacts enabled.

The workflow uses no repository secrets and uploads `test-results/` only when the gate
fails. The normal unit-test command remains fast and browser-independent.

## Documentation And Maintenance

The README documents:

- the normal unit-test command;
- browser dependency and Chromium installation;
- the separate browser test command;
- the artifact location and trace-viewer command;
- that browser tests are synthetic and never touch real queue state.

The browser install must be repeated after a Playwright upgrade because each Playwright
release expects a matching browser binary.

## Acceptance Criteria

- Default `uv run pytest` excludes browser tests and remains green without Chromium.
- The standalone browser command passes on the target Linux host outside the restricted
  sandbox.
- All three viewports pass behavior, overflow, and overlap checks.
- Fake join and cancellation exercise the real browser/API flow without real upstream
  requests.
- An unexpected external request, page error, or console error fails the suite.
- A deliberately broken layout or workflow produces a failing browser test and retained
  diagnostic artifacts.
- CI runs the same standalone browser gate without credentials.
