# Implementation decisions

## Data flow

1. The existing round WebSocket updates the spectator's authoritative game history.
2. A new move or round reload invalidates the old WDL immediately. The client requests
   `GET /game/:id/win-probability`; it never submits a FEN or moves to the public endpoint.
3. The Scala controller loads the live game, checks spectator access and standard variant,
   and passes its complete SAN history to the private prediction service.
4. A bounded Python scheduler coalesces requests by game, cancels superseded analysis,
   and shares cached results between spectators. Stockfish receives the full move stack.
5. The client accepts results only when game ID, SAN history, status and winner match.
   Pending results poll every 200 ms; ready results stop polling until another game event.

This keeps the existing socket protocol unchanged. Adding a custom WDL push protocol is
deferred until measurements justify maintaining changes in both lila and lila-ws.

## Correctness and failure handling

- WDL is always White / draw / Black, obtained from `PovWdl.white()`.
- Official game results bypass Stockfish in Scala and in the client. Aborts have no percentages.
- Historical replay hides the live-position percentages. Board orientation never changes meaning.
- The worker keeps at most 64 queued games and 512 cached predictions. The final local
  deployment uses two engines, each with one thread, 64 MiB hash and a 250 ms search,
  under a shared 2 CPU / 512 MiB container limit. The initial milestone used one engine.
- Cancellation discards the obsolete result while draining its bounded UCI search before
  reusing that engine; it must not cancel python-chess's ready handshake Future. Official
  results still bypass queued/active analysis without waiting for that drain.
- Scala coalesces identical requests for 150 ms, caps its cache at 1024 entries, and times out
  worker HTTP calls at 800 ms. Move processing never waits for the prediction service.
- UI requests time out at 1500 ms; unavailable/busy states retry after 2 s. Hidden tabs pause requests.
- Only the local reverse proxy is published, on `127.0.0.1:8080`; the worker stays internal.

## Scope and review

This milestone implements an engine estimate, not a model calibrated on human games.
The human outcome model, public hosting, other variants and analysis-page integration are deferred.
The live round page shows the official result when a watched game ends; a later page reload
may route to Lichess's separate analysis page.

Code and tests are AI-assisted using Codex. The user reviews and accepts this local fork.
No upstream issue, pull request or public deployment is part of this change.

## Verification

Meaningful regression cases cover point of view, repetition history, same-ply replacement,
rapid moves, duplicate spectators, cancellation, terminal results and engine failure recovery.
Report worker HTTP latency separately from browser end-to-end latency. A sub-second pilot
target is not a scalability claim: different concurrent games share bounded engine capacity.
