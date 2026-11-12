# Human outcome pilot

Extend the accepted engine WDL milestone with an explicitly experimental model of human
White win / draw / Black win outcomes. Use position evaluation, pre-game ratings, remaining
clocks, time control and game phase. Preserve spectator authorization and engine availability.

The delivery includes bounded, reproducible public-data sampling, a regularized multinomial
baseline, separate calibration and temporal test data, a JSON model artifact, evaluation
figures and model card, and live inference using authoritative server context.

This pilot does not claim calibration for the self-hosted player population. Clock-only
updates between moves, other variants and large-scale training remain later work.
