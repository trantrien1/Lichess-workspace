# Human outcome pilot — implementation and evidence

- [x] Add bounded PGN sampling with provenance, prefix-only features and regression tests.
- [x] Extract a shared training/serving feature schema with no result or future-state leakage.
- [x] Train on August 2026, calibrate on September, evaluate on October; disclose the repeated evaluation after a sampling defect was fixed.
- [x] Compare log loss, multiclass Brier score, reliability curves and rating/speed slices.
- [x] Export validated JSON coefficients and document sampling/engine/population limits.
- [x] Serve experimental predictions without extra Stockfish jobs or anonymous rating guesses.
- [x] Send authoritative ratings/clocks from Scala and show distinctly labelled results.
- [x] Verify suites, real inference, browser behavior, patch reproduction and handoff.

Selected approach: multinomial logistic regression plus temperature calibration. This permits
small, auditable JSON artifacts and standard-library-only serving. Training dependencies are
isolated in the Python `ml` dependency group.

Data: bounded prefixes of official standard rated game archives, selected analyzed blitz/rapid
human games. Train/validation/test months are fixed before inspecting results. They are pilot
samples, not uniform samples of entire months. Never tune hyperparameters on the test month.
Use complete games, fixed selected plies, equal weight per game, and discard partial final games.
Only currently known clocks/evaluation/history enter a feature vector; Result is a label only.

Serving gate: both ratings and both clocks present; supported speed/rating/ply range; valid
artifact; successful position evaluation. Official results always override all model output.
Promotion requires improvement over frequency, rating-only and engine-score WDL baselines on
the held-out test set; record game-level bootstrap uncertainty and all failed checks.

Before the first real fit, the numerical gates are fixed: at least 1000/300/300 games across
train/calibration/test, at least 20 test games in each outcome class, lower log loss and Brier
than all three baselines, a negative upper 95% bound for paired game-bootstrap log-loss
differences, and top-label ECE <= 0.06. C=1 is fixed; temperature is fit only on September.

Data extraction and feature/inference/training unit tests have passed after their observed
failures. October introduced `/game/` in PGN Site URLs; the parser now accepts both official
forms, with a regression test. Final samples contain 8000/2000/2000 games and
37997/9412/9341 positions. An initial all-even-ply sample was replaced with mixed parity
after coverage review; tests now require both sides to move. The first October result
was already inspected, so the model card explicitly records the repeated evaluation.

## Evidence collected on 2026-11-05

- `artifacts/human-python-final.log`: 55 tests passed, ruff passed. One pre-existing
  Starlette/httpx deprecation warning remains. Fail-first logs are `artifacts/human-*-red.log`.
- `reports/human-v1/evaluation.json`: all six predeclared gates pass; human log loss
  0.712218, Brier 0.455070, top-label ECE 0.021899. Temperature slightly worsens October
  compared with raw logistic output; this is disclosed, not tuned away.
- `reports/human-v1/model-card.md`: full provenance, bias, repeated-test disclosure,
  rating/speed slices, uncertainty and deployment limits. Reliability plot visually checked.
- `artifacts/human-reproduction.log`: `scripts/model.ps1 train` exits 0 and reproduces
  model ID `human-logit-v1-54213e4fef50` without replacing the serving artifact.
- `artifacts/human-worker-http.log`: real Stockfish 15.1, 21 positions, p95 289.1 ms;
  100 duplicate calls add zero evaluations. Different human contexts share the engine cache;
  absent context withholds the human output. Runtime has no numpy/sklearn.
- `reports/human-v1/engine-transfer.json`: 100 held-out games with fresh 250 ms engine
  scores all yield valid predictions. Mean largest-class shift 2.12 percentage points,
  maximum 32.19; a diagnostic, not proof of production calibration.

- `artifacts/human-scala-final.log`: `;scalafmtAll;scalafmtCheckAll;test;stage` exits 0;
  changed Scala source compiles and application is staged. sbt reuses passing test cache
  (`testQuick` reports no changed tests); the prior engine milestone ran 603 Scala tests.
  This is an incremental verification, not a claim that 603 tests reran in this milestone.
  Stage took 19m08s on the Windows bind mount. Read-only thread inspection showed cache
  serialization/filesystem work; it completed without terminating or bypassing checks.
- `artifacts/human-ui-final.log`: complete TypeScript/Sass build and all 264 frontend tests
  pass. `artifacts/human-ui-type-final.log`: type-aware lint, format check, and 19 focused
  panel tests pass after type-only cleanup. CSS stylelint also passed. Existing upstream
  Node test logs mention unavailable indexedDB; no tests failed and browsers report no exceptions.
- `artifacts/human-browser-smoke.json`: real registered local accounts, authoritative
  context, valid human probabilities, active-player endpoint denial, takeback/replacement,
  replay, reload, mobile layout, worker outage/recovery and official result all pass.
  Model ID matches shipped artifact; 20-move browser p95 484 ms. Screenshots
  `artifacts/human-panel.png` and `artifacts/human-mobile.png` visually checked.
- `artifacts/browser-smoke.json`: anonymous mode passes, p95 481 ms; engine remains
  available while absent ratings withhold human percentages. No browser exceptions.
- `patches/lila-win-probability.patch`: 13 feature files, whitespace check clean,
  reverse check against current checkout and forward cached check against pinned HEAD pass.
- `scripts/model.ps1 train` reproduces identical artifact bytes, SHA256
  `0822611ce98edcc7c3721483c430bce78c0df94c0ea5dc641e3fe09a69455fd0`.
- `scripts/dev.ps1 start` succeeds; local stack remains running at `http://localhost:8080`.
  Two local acceptance accounts and completed test games remain in the local database;
  mailer is mocked. Training data/build caches/logs are ignored, published model/report are kept.

All scoped pilot tasks are complete. Later work remains explicitly outside this milestone:
representative sampling, new untouched temporal evaluation, local-population calibration,
clock-only updates between moves and concurrency/load evaluation.

The user subsequently requested an autonomous final review and Vietnamese report. The
[final audit](final-audit.md) fixes reconnect and UCI cancellation races and adds a bounded
load probe (large-scale/long-running load work remains deferred). Latest evidence is in
`reports/acceptance/`: 56 Python tests, 264 frontend tests, registered/anonymous browser
flows, real official endings, two-worker configuration, and an unchanged model artifact.

## Requested UI simplification — 2026-11-05

- [x] Replace the two probability rows with one White/draw/Black bar and one percentage row.
- [x] Prefer the supported human estimate; use engine WDL otherwise, with a concise source
  caption. Remove the engine-name/depth heading. Official results keep precedence.
- [x] Update source-selection and browser acceptance checks, publish a full UI manifest,
  refresh the reproducible patch, and open a current demo for review.

Evidence: four view tests failed before the change; 20 focused tests and all 265 frontend
tests pass afterward. Full UI build, type-aware lint, formatting and stylelint pass.
Registered browser has 95 assertions, anonymous browser 115; zero browser exceptions.
The displayed values match the selected human/engine response, not merely a 100% total.
Both runs serve `round.GFD3AYWC.js`; model and backend are unchanged. Patch forward/reverse
checks pass for the same 13 feature files. See `reports/acceptance/ui-don-gian.md` and
`reports/acceptance/simple-panel/` for logs, source hashes and screenshots.
Fresh 20+5 demo: `http://localhost:8080/game/g54Kmpdg/white`, 20 plies, both API estimates
ready but only human values displayed. Browser opening was queued in Codex; headless
rendering and local HTTP were verified. The timed demo expires naturally.

## Explanation and arithmetic review — 2026-11-06

Explained the archived demo `g54Kmpdg` after `10...Nbd7` using the shipped model and the
archived +47 cp score. Exact request clocks were not retained in the old snapshot; an
explicit offline reconstruction uses 1500/1500 ratings and 1240 seconds each from the
saved screenshot. Maximum probability difference from the archive is 0.00003591, or
0.003591 percentage points; largest-remainder display is identical at 50.8/3.3/45.9.
This is not presented as a bit-exact replay of the original request. A stricter future
inference audit should retain the complete authoritative input context for its snapshots.

`reports/acceptance/demo-explanation/calculation.json` records all 23 features,
normalization, weighted contributions, temperature, logits and softmax outputs.
The in-conversation calculator matches Python within 1e-14 in six scenarios; browser
exceptions and mobile overflow are zero. No runtime code, model or dataset changed.
Docker Desktop was not running during this explanation; localhost:8080 answered as
Burp Suite, so historical position evidence was used without changing local services.

## GitHub branch publication — 2026-11-12

- [x] Publish the workspace to `trantrien1/Lichess-workspace` on the separate branch
  `codex/live-win-probability`, based on main commit
  `c090cb548411e763aa3bf7b9876253201322f78b`.
- [x] Verify the first published snapshot, commit
  `c74e4b619af17793aeaaecd6e53cee34faf76e59`, against `git ls-remote`.
- [x] Preserve all six existing vendored upstream directories unchanged; add 89
  workspace files, including source, model, patches, scripts, specifications and reports.
  Training data, caches, local upstream checkouts and runtime artifacts remain ignored.
- [x] Preserve the exported model and upstream patch bytes with scoped `.gitattributes`.
  Staged blob SHA256 matches the workspace: model
  `0822611ce98edcc7c3721483c430bce78c0df94c0ea5dc641e3fe09a69455fd0`, patch
  `3cd98fe15b4fff2e83771cd541c1fab479db87a0b68f08a05769736ced09280f`.

Fresh verification: 56 Python tests passed in 2.31 seconds, with the existing
Starlette/httpx deprecation warning; Ruff passed. The patch passed both reverse
checking against the working Lila checkout and forward cached checking with
`--whitespace=error-all` against its pinned upstream HEAD. The initial sandbox run
encountered a uv cache write error and stalled in the async cancellation test;
the complete frozen/offline test run outside the sandbox passed without code changes.

This was a source-publication task. Scala/TypeScript builds, browser acceptance,
live engine benchmarks and a fresh-machine bootstrap were not rerun. No application
behavior, model parameters or local service configuration was changed. Existing
pilot limitations above remain in force; this branch is not a public deployment.
