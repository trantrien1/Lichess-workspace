# Final audit requested by the user — 2026-11-05

Scope: independently inspect the completed local pilot, improve defects backed by evidence,
run acceptance without requiring manual user participation, and produce a Vietnamese report.
Do not keep iterating after the scoped checks pass. Do not tune the model on inspected test data.

Stop conditions:
- [x] Worker suite, feature lint/format and real engine HTTP checks pass on the final source.
- [x] Registered and anonymous browser flows pass, including a real network reconnect.
- [x] Official checkmate, draw, resignation, timeout and aborted results are exercised in the browser.
- [x] A bounded multi-game/duplicate-spectator probe records correctness, latency and queue limits.
- [x] Model/data and patch provenance are consistent; new findings are fixed or explicitly bounded.
- [x] Vietnamese acceptance report links evidence, states limits and records final service health.

Initial review: existing main pilot evidence is complete. The previous browser flow tested
reload and worker restart, but real browser-network reconnect and non-resignation endings
were represented only in lower-level tests. These are the additional acceptance targets.
Inspection of all 56,750 sampled FENs found no checkmate, stalemate or insufficient-material
terminal positions; no model/data change is justified by that check.

## Findings and resolution

1. Initial socket.open unnecessarily called RoundSocket.reload; going offline while that
   request failed caused a full-page error navigation. Reload now runs only after the
   panel entered stale state. Real offline/online acceptance passes; registered browser
   additionally counts main-frame navigations and requires zero during reconnect.
2. Cancelling python-chess analysis during readyok could corrupt its command Future and
   break the next game. A deterministic test with real UciProtocol failed with
   InvalidStateError before the fix. Shielding and draining the bounded search, plus
   preventing repeated cancellation, passes the regression and real saturation probes.
3. Package-only UI builds do not update asset manifest by upstream design. A full build
   generated round.X2T6IZS5.js; browser acceptance now verifies the rendered bundle hash.

## Final evidence

- 56 Python tests and 264 full frontend tests pass. Type-aware lint, format, stylelint and
  full frontend build pass. Evidence copied under `reports/acceptance/evidence/`.
- Registered browser: 90 assertions, p95 554 ms over the first 20 plies. Anonymous: 91
  assertions, p95 561 ms; includes a real 15-second clock timeout. Both have zero browser
  exceptions. Human mode includes worker outage/recovery and zero-navigation reconnect.
- Real HTTP worker: 21 positions, p95 279 ms; 100 duplicate cached requests add zero
  engine evaluations. Human context changes reuse the same engine result.
- Four-position/100-request isolated burst: one worker 1061.2 ms, two workers 587.1 ms
  for the last ready result. Both compute exactly four positions. 96-game saturation
  produces bounded busy responses; all official aborts return and the queue drains with
  zero engine/model errors. Two workers are selected within 2 CPU / 512 MiB.
- Model SHA256 remains 0822611ce98edcc7c3721483c430bce78c0df94c0ea5dc641e3fe09a69455fd0.
  All three dataset hashes match manifests. Patch forward/reverse checks pass.
- Vietnamese report: `reports/acceptance/bao-cao-nghiem-thu.md`. Local service remains
  available; final health and source hashes are recorded in the evidence directory.

Stop condition reached. No scoped acceptance issue remains open. Data/calibration and
capacity limitations remain explicit; there is no claim of broad production readiness.

## User demo handoff — 2026-11-05

Created local registered-player game `FuEwSngw`, casual rapid 20+5, and played 20 plies.
Spectator URL: `http://localhost:8080/game/FuEwSngw/white`. Both engine and human predictions
were ready; browser reported zero exceptions. Evidence: `artifacts/demo/demo.json` and
`artifacts/demo/demo.png`. Opening the URL in the Codex right panel was queued successfully;
the in-app inspection tool could not initialize, so visible tab activation was not verified.
This is a live timed demo and will expire naturally; it is not a permanent fixture. Production
source and model are unchanged. The helper `artifacts/open-demo.mjs` can prepare a fresh demo.
