# Live win/draw/loss for self-hosted Lichess

Add a live probability display to standard-chess spectator boards. The first release uses Stockfish WDL and explicitly labels it “Theo engine”. A later milestone may estimate human outcomes using ratings and clocks; it is not part of this first engine-based delivery.

The feature must not slow or interrupt play when evaluation is unavailable. Scope includes a reproducible local Lichess environment, bounded prediction worker, server-side integration, spectator UI, regression tests, and runnable acceptance evidence.

No new chess UI framework, public hosting, full game-database download, or ML training is required for this milestone.
