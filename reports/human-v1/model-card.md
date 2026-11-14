# Human outcome pilot v1

Artifact: `human-logit-v1-54213e4fef50`, evaluated 05/11/2026. This is an experimental
prediction of White win / draw / Black win in human games. The compact UI prefers this
estimate when supported and otherwise displays Stockfish WDL, with a caption identifying
the selected source. These outputs are not blended. It has not been calibrated for the
local server's population.

## Data and evaluation protocol

Official [Lichess standard-game archives](https://database.lichess.org/#standard_games)
are distributed under CC0 and provide ratings, clocks and engine annotations for a
subset of games. We stream a bounded prefix of each archive and keep complete, analyzed,
rated standard blitz/rapid games, excluding players marked BOT. Missing ratings,
unsupported time controls, malformed moves and incomplete final records are rejected.
The underlying ratings are Lichess Glicko-2 ratings.

| Split | Month | Games | Positions | Actual UTC time span on day 1 |
|---|---|---:|---:|---|
| Training | August 2026 | 8,000 | 37,997 | 00:00:01–01:29:36 |
| Temperature calibration | September 2026 | 2,000 | 9,412 | 00:00:00–00:19:31 |
| Evaluation | October 2026 | 2,000 | 9,341 | 00:00:01–00:19:59 |

These are **prefix samples, not representative samples of whole months**. Selecting games
with analysis also introduces selection bias. October has 970 White wins, 61 draws and
969 Black wins. Game IDs cannot overlap across splits; timestamp ranges must be ordered.
Raw data are ignored by Git. URLs, byte limits, consumed-prefix hashes, dataset hashes,
timestamps and selection rules are preserved in [data-provenance.json](data-provenance.json).
Total compressed bytes consumed were about 56.5 MB; full monthly archives were not downloaded.

Fixed sampled plies are 4, 13, 24, 41, 60, 81, 100 and 139. A game contributes equal total
weight regardless of its number of sampled positions. Features use the SAN prefix, current
white-oriented engine score, known clocks, initial ratings and time control. The eventual
result is only a target; termination, rating change, future clocks/evaluations and player
identity are excluded. Feature extraction is shared between training and serving.

An initial extraction selected only even plies. Its metrics were inspected before that
coverage defect was discovered. We added regression tests, resampled both sides to move
and repeated October evaluation. The first report remains in local
`artifacts/human-initial-evaluation.json`. Consequently October was inspected twice;
this is not a pristine one-use holdout. Model parameters, month boundaries and numerical
acceptance gates were not tuned in response. A future promotion needs a new untouched
period and representative local data.

## Model and fixed gates

The model is multinomial logistic regression with 23 standardized features, fixed C=1,
followed by one temperature fitted only on September. It includes score, material,
side to move, ply, repetition, rating difference/mean, clocks and score/context interactions.
The exported artifact is validated JSON with coefficients, scaler, class/feature order,
support range and provenance. Serving uses no NumPy, sklearn or pickle.

Before the first fit, gates were recorded in the feature task list: at least 1,000/300/300
games; at least 20 October games per class; lower game-weighted log loss and multiclass
Brier than all three baselines; negative upper 95% bound for paired game-bootstrap
log-loss differences; top-label ECE ≤ 0.06. All passed for this pilot. They are engineering
acceptance criteria, not evidence of general calibration or causal effects.

## Results

Lower is better. Multiclass Brier is the sum of the three squared errors, range 0–2.
ECE uses 10 equal-width bins of the most likely class, not classwise draw calibration.

| Candidate | Log loss | Brier | Top-label ECE |
|---|---:|---:|---:|
| Human model, temperature calibrated | 0.712218 | 0.455070 | 0.021899 |
| Same logistic model before temperature | 0.712142 | 0.455004 | 0.021337 |
| Training outcome frequency | 0.809178 | 0.529755 | 0.017375 |
| Rating-only heuristic | 0.827775 | 0.535523 | 0.061436 |
| Archived score → Stockfish 15.1 WDL formula | 6.974149 | 1.178645 | 0.587609 |

The human model improves log loss by about 12.0% and Brier by 14.1% against training
frequency. The paired log-loss difference is −0.09696, with game-bootstrap 95% interval
[−0.10717, −0.08632], 500 resamples. It also beats the rating heuristic and score-WDL
baseline under the same protocol. These are simple baselines, not a comparison with a
tuned tree or neural model. The rating heuristic uses an Elo-style expectation with a
training draw prior; it is not a fitted Glicko-2 outcome model.

Temperature 0.993231 reduces September log loss from 0.711665 to 0.711658, but **slightly
worsens all three October metrics** versus uncalibrated logistic regression. We retain
the predefined protocol rather than select a variant on October. Frequency also has
lower top-label ECE despite worse predictive losses. As explained in
[sklearn's calibration documentation](https://scikit-learn.org/stable/modules/calibration.html),
log loss and Brier combine calibration with discrimination; lower loss alone does not
establish better calibration.

| October slice | Games | Human log loss | Human Brier |
|---|---:|---:|---:|
| Blitz | 1,221 | 0.70 | 0.45 |
| Rapid | 779 | 0.73 | 0.46 |
| Mean rating 600–1399 | 720 | 0.67 | 0.44 |
| Mean rating 1400–1799 | 711 | 0.71 | 0.46 |
| Mean rating 1800–2199 | 442 | 0.78 | 0.48 |
| Mean rating 2200–3000 | 127 | 0.73 | 0.45 |

Full precision, baseline slices, confidence intervals and per-bin weighted support are in
[evaluation.json](evaluation.json). The [reliability plot](calibration.png) has been visually
checked. Draw reliability at high predicted probabilities is noisy because support is
small; the 61 draw games are insufficient for a strong draw-calibration claim.

## Production-engine transfer diagnostic

The PGNs do not specify a consistent engine version/search budget. Serving uses Stockfish
15.1, one thread, 64 MiB hash and a 250 ms search. To check transfer, we selected 100 October
games by hashed ID, one hash-selected position per game, without looking at outcomes.
All 100 produced valid human probabilities with fresh production-engine scores.

| Human inference on those same 100 positions | Log loss | Brier |
|---|---:|---:|
| Archived PGN score | 0.780479 | 0.475629 |
| Fresh 250 ms engine score | 0.782700 | 0.476336 |

The mean of each position's largest class-probability change is 2.12 percentage points;
the maximum is 32.19 points. This small probe, including only 6 draws, does not establish
runtime calibration. See [engine-transfer.json](engine-transfer.json). Engine timings may
produce slightly different scores on another machine. No parameter was fitted using this probe.

## Serving behavior and limits

- Standard blitz/rapid only; both ratings 600–3000; ply 4–140; initial time 30–1800 seconds;
  increment at most 60 seconds; both current clocks must be known.
- Ratings and clocks come from authoritative Lichess game state. Anonymous players get no
  invented rating. New registered users do have Lichess's provisional initial rating;
  uncertainty in that rating is not modeled separately in v1.
- The UI shows one bar and three percentages. Supported human output is captioned
  “Người chơi · thử nghiệm”; engine fallback is captioned “Ước tính từ thế cờ” with an
  engine explanation on hover. Engine name/depth diagnostics are omitted. Official
  terminal results override both. Replay, disconnect and stale-response rules apply to both.
- Predictions update on moves/reconnects. Clock decay between moves is not continuously
  re-evaluated. Ratings/clocks do not enter the engine cache key: current human context
  is applied to a shared engine evaluation without another Stockfish job.
- A missing, rejected or invalid model fails closed for human output while engine output
  remains available. JSON dimensions, finite values, feature/class order and support bounds
  are checked; `approved_for_pilot` must be true. Worker `/health` reports model ID/errors.
- Training data are rated games, while the browser acceptance uses casual local games.
  Calibration on casual games, provisional users, high concurrency or this server's future
  population remains unestablished. This is a local experimental deployment.

## Reproduce

From the workspace root with `uv` installed:

```powershell
.\scripts\model.ps1 collect
.\scripts\model.ps1 train
```

`collect` skips existing data, refuses partial data without a manifest, and respects byte/game
limits. `train` checks dataset hashes and temporal/game separation. Its output goes to
`artifacts/human-reproduction/`, preserving the shipped artifact. The pinned environment is
`services/prediction/uv.lock`, group `ml`. Recollection depends on the upstream archive
remaining unchanged; compare manifests to the committed provenance.

The shipped file is `services/prediction/models/human-v1.json`. Rebuild the prediction image
after deliberately replacing it. The engine-transfer diagnostic is reproducible with:

```powershell
docker compose -f infra/compose.yml run --rm --no-deps `
  -v "${PWD}/scripts:/checks:ro" -v "${PWD}/data/human:/data:ro" `
  prediction python /checks/probe_engine_transfer.py --data /data/2026-10-balanced.jsonl
```

Code, tests and this report were prepared with Codex assistance. No upstream contribution
or public deployment was made.

## Final operational audit

The later local acceptance audit retains the same model bytes and evaluation data. It fixes
a socket reconnect race and a UCI cancellation race, then selects two engine workers under
the existing 2 CPU / 512 MiB container limit. Each search still uses one thread, 64 MiB hash
and 250 ms. See the [Vietnamese acceptance report](../acceptance/bao-cao-nghiem-thu.md) for
fresh suites, browser endings/reconnect checks, bounded concurrency measurements and evidence.
