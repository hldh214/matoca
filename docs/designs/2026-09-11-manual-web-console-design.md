# Manual Japanese Web Console Design

## Goal

Make Matoca a fast, practical Japanese console for checking every supported merchant,
comparing all current shops, seeing an active queue, and joining a queue with the minimum
necessary input. This phase does not expose unfinished prediction or automation features.

## Scope

- Keep the home page as a merchant selector.
- Support `炭焼きレストラン さわやか` and `ラ・オハナ 横浜本牧` with their full names.
- Rebuild the merchant page around current queue status and a dense shop comparison list.
- Default to shops that can currently accept a queue request.
- Show current waiting groups and Matoca's official waiting-time estimate.
- Provide a compact manual join dialog and queue cancellation flow.
- Put global party-size settings in the application header and persist them through the
  existing SQLite preferences repository.
- Keep all visible interface text Japanese.

## Non-Goals

- Do not display an automatic queue button until task scheduling exists end to end.
- Do not label Matoca's official estimate as a locally trained prediction.
- Do not add regional grouping, including Sawayaka-specific region navigation.
- Do not add application authentication; Cloudflare Zero Trust remains the entry guard.
- Do not change queue protocol payloads, token handling, or merchant authentication.

## Information Architecture

### Merchant Selector

The first screen is an operational merchant selector, not a landing page. The header shows
`Matoca`, the page title is `利用する加盟店を選ぶ`, and each merchant is one image-backed
row with its full name and a `店舗を見る` action. Do not use `ブランド` in visible copy.
The supported merchant imagery already comes from the built-in merchant registry.

### Merchant Console

The first viewport contains, in order:

1. A compact application header with back navigation, merchant identity, `設定`, and a
   refresh icon.
2. A persistent current-queue band. It always has a stable height and shows loading,
   no-current-queue, active queue, or read-error state without moving the shop list.
3. A toolbar with the `受付可能` / `すべて` segmented filter, available/total counts, and
   text search.
4. A dense shop list with stable columns for store,受付状況,現在の待ち組数,公式目安, and
   the primary action.

Desktop rows prioritize rapid comparison. Below 760 px, each shop becomes a compact
two-column layout with identity and action spanning the width. There is no region sidebar,
floating settings panel, hero, or nested card layout.

## Shop Status

The backend presentation response must resolve a stable status code and Japanese label:

- `available`: `受付可能`
- `closed`: `営業時間外`
- `holiday`: `休業`
- `suspended`: `受付停止`
- `stale`: `更新待ち`

`can_join` is true only for a detail-fresh, open, issuable, non-holiday, non-suspended
shop. The browser renders this decision but does not reconstruct safety from nullable
booleans. A stale or unknown shop remains visible under `すべて` and cannot be submitted.

The waiting-time column is labeled `公式目安`. It displays `約N分`, `N分以上`, or `—`.
No fast/typical prediction labels appear in this phase.

## Current Queue

Queue state is loaded independently from the cached shop catalog and is never overwritten
by a catalog refresh. An active queue displays the store name, reception number, groups
ahead, official estimate when available, and a clear cancellation command. Join buttons
remain disabled until the current-queue check completes. Create and cancel responses update
the band immediately, then reconcile with a fresh queue read.

## Manual Join Flow

Clicking `今すぐ受付` opens one dialog for the selected shop. It shows current groups and
official estimate, then only fields required by that shop's live form:

- Adult count defaults to the global preference, initially 2.
- Child count defaults to the global preference, initially 0, and is hidden when unsupported.
- Values are clamped to the shop's live minimum and maximum.
- Sawayaka's sole enabled missed-call confirmation is selected by default.
- Unsupported multi-choice requirements disable submission and display
  `選択内容の確認が必要です`.

Submitting always uses the existing server-side live detail and queue validation. The Web
UI never claims success before the server response.

## Settings

The header `設定` button opens a small dialog for global adult and child defaults. It reads
and writes the existing singleton SQLite preferences row through same-origin JSON APIs.
Prediction tolerances remain stored but are not shown or modified in this manual phase.
Settings affect newly opened join dialogs and never rewrite a dialog already being edited.

## API And Modules

Add a cached, presentation-specific merchant console endpoint. It combines stored shops,
catalog freshness, and merchant identity without contacting Matoca. Keep the current queue
endpoint separate because it is live user state.

Split the browser code into small ES modules:

- `api.js`: same-origin requests, timezone header, structured Japanese errors.
- `shop-list.js`: safe DOM rendering, filter/search, and action state.
- `queue-status.js`: stable current-queue rendering and response revision handling.
- `join-form.js`: shop-specific fields, defaults, and create/cancel interactions.
- `preferences.js`: settings dialog and global defaults.
- `merchant.js`: page orchestration and refresh cadence only.

Dynamic upstream strings are inserted with DOM `textContent`, not HTML interpolation. No
token, response body, or subscription data is logged.

## Refresh And Failure Behavior

- Load the cached console immediately on entry.
- Refresh cached console data every 30 seconds while visible.
- Read current queue independently every 30 seconds and after visibility returns.
- Manual refresh wakes the existing collection coordinator, then reloads cached data.
- Automatic browser refresh never starts an extra upstream collection.
- Preserve the last successful data during transient errors and show a compact Japanese
  stale/error indicator.

## Verification

- Unit-test status mapping, freshness, preference APIs, shop-form defaults, and all error
  states without real upstream requests.
- Parse every JavaScript module with Node and exercise state ownership using the existing
  synthetic DOM harness.
- Run the full Python quality gate with uv.
- Start the Supervisor service only for deployment verification.
- Verify 1440x900 and 390x844 layouts with Playwright screenshots, including no horizontal
  overflow, stable queue band height, working dialogs, and visible next-section content.
- Browser verification must never create or cancel a real queue.
