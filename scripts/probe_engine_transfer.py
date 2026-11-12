"""Bounded inference probe using fresh production-engine scores on held-out games.

This is a diagnostic, not an additional fitting step or a full calibration evaluation.
Run inside the prediction image with the dataset mounted read-only.
"""

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/app")

from app.engine import Stockfish  # noqa: E402
from app.human import load_model  # noqa: E402
from app.models import PositionRequest  # noqa: E402


def scores(labels, predictions):
    return {
        "log_loss": sum(
            -math.log(max(p[y], 1e-15)) for y, p in zip(labels, predictions, strict=True)
        )
        / len(labels),
        "brier": sum(
            sum((v - float(i == y)) ** 2 for i, v in enumerate(p))
            for y, p in zip(labels, predictions, strict=True)
        )
        / len(labels),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=Path("/app/models/human-v1.json"))
    parser.add_argument("--games", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.games <= 256:
        parser.error("Use 1..256 games for this bounded diagnostic")
    raw = args.data.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = json.loads(args.data.with_suffix(".manifest.json").read_text())
    assert digest == manifest["dataset_sha256"], "Dataset checksum mismatch"
    groups = defaultdict(list)
    for line in raw.splitlines():
        row = json.loads(line)
        groups[row["game_id"]].append(row)
    # Select games and one position per game without looking at labels or evaluations.
    selected = []
    for game_id in sorted(groups, key=lambda g: hashlib.sha256(g.encode()).hexdigest())[
        : args.games
    ]:
        rows = groups[game_id]
        index = int.from_bytes(
            hashlib.sha256((game_id + ":position").encode()).digest()[:4], "big"
        ) % len(rows)
        selected.append(rows[index])
    model = load_model(args.model)
    engine = Stockfish(250)
    labels, archived, fresh, engine_wdl, deltas = [], [], [], [], []
    started = time.monotonic()
    try:
        for row in selected:
            position = PositionRequest(
                game_id=row["game_id"], moves=row["moves"], context=row["context"]
            )
            board = position.board()
            assert board.fen() == row["fen"]
            evaluation = await engine.evaluate(board, row["game_id"])
            old = model.predict(board, position.context, row["evaluation_cp"], row["mate"])
            new = model.predict(board, position.context, evaluation.evaluation_cp, evaluation.mate)
            assert old.status == new.status == "ready", (old, new)
            archived.append(list(old.probabilities.model_dump().values()))
            fresh.append(list(new.probabilities.model_dump().values()))
            engine_wdl.append(list(evaluation.probabilities.model_dump().values()))
            labels.append(row["label"])
            deltas.append(max(abs(a - b) for a, b in zip(archived[-1], fresh[-1], strict=True)))
    finally:
        await engine.close()
    print(
        json.dumps(
            {
                "purpose": "Inference diagnostic; does not establish deployment calibration",
                "model_id": model.artifact.model_id,
                "dataset_sha256": digest,
                "sampling": (
                    "Hash-selected games and one position per game; no outcome selection"
                ),
                "engine": engine.name,
                "search_ms": 250,
                "games": len(selected),
                "labels_white_draw_black": [labels.count(i) for i in range(3)],
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "metrics": {
                    "human_archived_score": scores(labels, archived),
                    "human_fresh_score": scores(labels, fresh),
                    "fresh_engine_wdl": scores(labels, engine_wdl),
                },
                "mean_largest_class_probability_shift": sum(deltas) / len(deltas),
                "max_class_probability_shift": max(deltas),
            },
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
