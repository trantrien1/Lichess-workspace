# Requirements

## Engine estimate
- For standard chess, display White win, draw, and Black win probabilities, with a visible engine-estimate label.
- All values must be finite and non-negative and sum to one; display rounding must sum to 100%.
- Correctly transform engine side-to-move WDL to the White perspective.
- Supply move history to Stockfish so repeated positions and move counters are interpreted correctly.

## Freshness and lifecycle
- Refresh after authoritative moves and reconnects. A takeback followed by another move must not reuse an unrelated result at the same ply.
- Distinguish pending, ready, stale/unavailable, and finished states.
- Official server results for checkmate, resignation, timeout, and draw override engine predictions. Aborted or unfinished games must not invent a winner.

## Isolation and access
- Serve predictions to spectators; enforce access on the server. Active players must not receive predictions through the feature endpoint.
- The worker accepts authoritative internal requests, is not published publicly, and has bounded CPU, memory, concurrency, queue length, cache, and search duration.
- Share evaluation of a game position across viewers. Superseded requests must not overwrite a newer position.
- Worker failures, timeouts, and queue saturation must not impair gameplay.

## Acceptance
- Start the local stack from documented commands and play a game between two browser sessions.
- A spectator sees WDL update and can reload/reconnect without incorrect position results.
- Verify the White/Black perspective, takebacks, official results, and unavailable-worker behavior.
- Run unit/integration checks for numerical and scheduling invariants, upstream checks for changed code, and browser smoke checks.
- Report measured latency and test conditions. P95 under one second at pilot load is a target, not a claim before measurement.
