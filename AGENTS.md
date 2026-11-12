# Lichess win probability

## Scope and source of truth
- This workspace builds a self-hosted Lichess fork. Read `openspec/changes/live-win-probability/` for the engine milestone and `openspec/changes/human-win-probability/` for the human-model pilot.
- Keep live Stockfish WDL and experimental human-outcome probabilities distinct. Human-model scope, evaluation and sampling limits are in `reports/human-v1/model-card.md`; neither output is established as calibrated for the local population.
- Upstream checkouts live under `infra/lila-docker/`. Track exact revisions in `upstream-lock.json`; preserve modifications as patches in `patches/` so setup is reproducible.
- Keep Lichess Scala and TypeScript/Snabbdom conventions. The Python prediction service runs independently from gameplay.

## Working agreement
- Use the project-local Superpowers skills on demand: `.agents/skills/systematic-debugging/SKILL.md` for failures, `.agents/skills/test-driven-development/SKILL.md` when adding behavioral regression tests, and `.agents/skills/verification-before-completion/SKILL.md` before completion claims. Exact source revision is in `workflow-lock.json`.
- Start from the feature task list and inspect only the current task's code. Keep concise evidence there for the next session; do not repeatedly reload the whole repository or every skill.
- Implement and verify within the requested scope without asking to continue between routine steps. Ask only when missing information changes the intended outcome materially.
- Inspect relevant code and interfaces, use focused checks, and retain mandatory upstream checks. Avoid unrelated refactors and duplicate test frameworks.
- Record completed work, evidence, and remaining blockers in the feature task list. Never relax acceptance criteria just to mark work complete.
- Do not claim a test, build, benchmark, or browser flow passed unless it actually ran and its result was checked.
- Final handoff: implemented behavior, key files, checks and limitations, local URL and reproduction steps.

## Domain invariants
- Probabilities always refer to White win / draw / Black win, regardless of side to move or board orientation, and sum to one.
- Identify positions by game plus full position history, not ply alone. Discard obsolete responses after moves, takebacks, reconnects, or completion.
- Use authoritative server game state. Official terminal results override engine output.
- Human features use only information available at the current position. Never invent anonymous ratings, reuse future clocks or put outcomes/player identity into features. Keep game/time splits disjoint and record any reuse of a test set.
- Engine work must be bounded and must never block the move-processing loop. Multiple spectators share work for a game position.
- Enforce feature access at the server and keep the prediction worker off public interfaces.
- The development stack is local-only; public deployment requires its own configuration.
