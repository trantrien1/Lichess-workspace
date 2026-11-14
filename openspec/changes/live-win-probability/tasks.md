# Implementation and evidence

- [x] Pin upstream repositories and establish a reproducible local stack.
- [x] Run Lichess and verify the core game flow between browser sessions.
- [x] Build the bounded Stockfish WDL prediction service and meaningful regression tests.
- [x] Integrate authoritative game state and server-side spectator access.
- [x] Add the probability panel using existing Lichess UI conventions.
- [x] Verify stale responses, takebacks, reconnect, official results, and worker failures.
- [x] Run relevant Scala/TypeScript/Python checks and browser acceptance.
- [x] Save patches, exact versions, startup/check scripts, and handoff instructions.

## Verified delivery — 2026-11-05
Python: 19 tests passed; real Stockfish 15.1 HTTP smoke passed for 21 positions.
Worker HTTP p95 was 268.6 ms with 50 ms polling; first position was 528.7 ms.
100 requests for a cached position caused zero extra evaluations. These are worker-only measurements.
Evidence: `artifacts/check-final.log`, `artifacts/prediction-smoke.json`.
Pytest reports one upstream Starlette deprecation warning about its httpx TestClient;
all tests pass. This warning does not affect the running worker.

Scala: 603 tests passed, zero failures/errors; scalafmtCheckAll and stage passed (exit 0).
Evidence: `artifacts/scala-checks-final.log`.
The full UI build passed. Frontend: 260 tests passed across 34 suites,
including 8 WDL regression cases; changed UI files passed oxlint and oxfmt.
Browser acceptance passed with two anonymous players and a spectator: 20 initial plies,
takeback and same-ply replacement, orientation/replay/reload, mobile layout, worker outage
and recovery, player endpoint denial, and the official resignation result. Browser p95 was
504 ms for the first 20 moves on the final precompiled runtime, measured from the move
interaction until WDL appeared. The earlier development-mode run measured 490 ms.
These local pilot measurements do not establish capacity across many simultaneous games.
Evidence: `artifacts/ui-build.log`, `artifacts/ui-suite.log`, `artifacts/browser-smoke-final.log`,
`artifacts/browser-smoke.json`
and the `artifacts/wdl-*.png` screenshots.

The feature patch applies to pinned upstream HEAD and reverses from the working checkout
(`artifacts/patch-check.log`). Pinned Docker images built successfully.
The documented `start`, `check`, and `browser -WithOutage` commands passed. The application
is running at http://localhost:8080 using staged JARs, without a persistent sbt process.
Its observed memory use after startup was approximately 627 MiB; JVM maximum heap is 1 GiB.
Evidence: `artifacts/docker-build-final.log`, `artifacts/start-verified.log`.
Bootstrap components were exercised on this workspace; a second clean-machine bootstrap
was not repeated. Windows CRLF sources are normalized by the preparation script, and the
readiness probe uses IPv4 to match Docker's local bind.

The integration was added during the first dependency/build run; an unmodified browser baseline
was not separately run. Core gameplay was verified alongside the feature.

Project-local Superpowers skills installed: systematic-debugging, test-driven-development,
verification-before-completion. See `workflow-lock.json`; existing work is not retroactively
claimed to have followed their full process.
