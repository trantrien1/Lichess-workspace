"""Fit, calibrate and evaluate a portable multinomial human-outcome model."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import chess
import chess.engine
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from app.human import FEATURE_NAMES, HumanModel, extract_features
from app.models import HumanContext


def game_weights(rows):
    counts = Counter(row["game_id"] for row in rows)
    return np.array([1 / counts[row["game_id"]] for row in rows])


def validate_splits(train, calibration, test):
    if any(not rows for rows in (train, calibration, test)):
        raise ValueError("All splits must contain games")
    ids = [set(row["game_id"] for row in rows) for rows in (train, calibration, test)]
    if ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2]:
        raise ValueError("Game overlap across dataset splits")
    for earlier, later in ((train, calibration), (calibration, test)):
        if max(row["played_at"] for row in earlier) >= min(row["played_at"] for row in later):
            raise ValueError("Splits must be strictly chronological")


def matrix(rows):
    values, previous, game_id = [], [], None
    board = chess.Board()
    for row in rows:
        moves = row["moves"]
        if game_id != row["game_id"] or moves[: len(previous)] != previous:
            board, previous = chess.Board(), []
        for san in moves[len(previous) :]:
            board.push_san(san)
        if board.fen() != row["fen"]:
            raise ValueError("Snapshot FEN does not match its move history")
        features = extract_features(
            board, HumanContext(**row["context"]), row["evaluation_cp"], row.get("mate")
        )
        values.append([features[name] for name in FEATURE_NAMES])
        previous, game_id = moves, row["game_id"]
    return np.array(values)


def metrics(labels, probabilities, weights):
    y, p, w = np.asarray(labels), np.asarray(probabilities), np.asarray(weights)
    if (
        p.shape != (len(y), 3)
        or not np.isfinite(p).all()
        or np.any(p < 0)
        or not np.allclose(p.sum(axis=1), 1)
    ):
        raise ValueError("Invalid probability matrix")
    losses = -np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1))
    brier = np.sum((p - np.eye(3)[y]) ** 2, axis=1)
    confidence, correct = p.max(axis=1), p.argmax(axis=1) == y
    ece = 0.0
    for index in range(10):
        mask = np.minimum((confidence * 10).astype(int), 9) == index
        if mask.any():
            ece += (
                w[mask].sum()
                / w.sum()
                * abs(np.average(confidence[mask] - correct[mask], weights=w[mask]))
            )
    return {
        "log_loss": float(np.average(losses, weights=w)),
        "brier": float(np.average(brier, weights=w)),
        "top_label_ece": float(ece),
    }


def fit_model(train, calibration):
    x, validation_x = matrix(train), matrix(calibration)
    if set(x[:, list(FEATURE_NAMES).index("white_turn")]) != {-1, 1}:
        raise ValueError("Training must cover both sides to move")
    y = np.array([row["label"] for row in train])
    validation_y = np.array([row["label"] for row in calibration])
    if set(y) != {0, 1, 2} or set(validation_y) != {0, 1, 2}:
        raise ValueError("Training and calibration need all three outcomes")
    weights, validation_weights = game_weights(train), game_weights(calibration)
    # Two-pass weighted variance avoids cancellation for constant columns.
    mean = np.average(x, axis=0, weights=weights)
    scale = np.sqrt(np.average((x - mean) ** 2, axis=0, weights=weights))
    scale[scale < 1e-12] = 1
    classifier = LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000, tol=1e-7)
    with threadpool_limits(limits=2):
        classifier.fit((x - mean) / scale, y, sample_weight=weights)
    logits = classifier.decision_function((validation_x - mean) / scale)

    def objective(temperature):
        return metrics(validation_y, softmax(logits / temperature, axis=1), validation_weights)[
            "log_loss"
        ]

    calibration_fit = minimize_scalar(objective, bounds=(0.25, 4.0), method="bounded")
    temperature = (
        float(calibration_fit.x)
        if calibration_fit.success and calibration_fit.fun < objective(1)
        else 1.0
    )
    coefficients = classifier.coef_.tolist()
    model_hash = hashlib.sha256(
        json.dumps([coefficients, mean.tolist(), temperature]).encode()
    ).hexdigest()[:12]
    return {
        "schema_version": 1,
        "model_id": "human-logit-v1-" + model_hash,
        "feature_names": list(FEATURE_NAMES),
        "classes": ["white", "draw", "black"],
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "coefficients": coefficients,
        "intercept": classifier.intercept_.tolist(),
        "temperature": temperature,
        "approved_for_pilot": False,
        "support": {"min_rating": 600, "max_rating": 3000, "min_ply": 4, "max_ply": 140},
        "metadata": {
            "algorithm": (
                "multinomial logistic regression; C=1; temperature on separate calibration month"
            ),
            "train_games": len({row["game_id"] for row in train}),
            "calibration_games": len({row["game_id"] for row in calibration}),
            "calibration_log_loss_before": objective(1),
            "calibration_log_loss_after": objective(temperature),
        },
    }


def predict_matrix(artifact, x, temperature=None):
    normalized = (x - np.array(artifact["mean"])) / np.array(artifact["scale"])
    logits = normalized @ np.array(artifact["coefficients"]).T + np.array(artifact["intercept"])
    return softmax(logits / (temperature or artifact["temperature"]), axis=1)


def baselines(train, rows):
    weights = game_weights(train)
    frequencies = (
        np.bincount([r["label"] for r in train], weights=weights, minlength=3) / weights.sum()
    )
    rating, engine = [], []
    for row in rows:
        c = row["context"]
        expected = 1 / (1 + 10 ** ((c["black_rating"] - c["white_rating"]) / 400))
        rating.append(
            [(1 - frequencies[1]) * expected, frequencies[1], (1 - frequencies[1]) * (1 - expected)]
        )
        score = (
            chess.engine.Mate(row["mate"])
            if row.get("mate") is not None
            else chess.engine.Cp(row["evaluation_cp"])
        )
        wdl = score.wdl(model="sf15.1", ply=len(row["moves"]))
        engine.append([wdl.wins / 1000, wdl.draws / 1000, wdl.losses / 1000])
    return {
        "frequency": np.tile(frequencies, (len(rows), 1)),
        "rating_only": np.array(rating),
        "score_wdl_sf15.1": np.array(engine),
    }


def bootstrap_delta(rows, candidate, baseline):
    groups = defaultdict(list)
    for row, proposed, original in zip(rows, candidate, baseline, strict=True):
        label = row["label"]
        groups[row["game_id"]].append(
            -np.log(max(proposed[label], 1e-12)) + np.log(max(original[label], 1e-12))
        )
    deltas = np.array([np.mean(values) for values in groups.values()])
    rng = np.random.default_rng(20261105)
    sampled = np.array(
        [np.mean(rng.choice(deltas, size=len(deltas), replace=True)) for _ in range(500)]
    )
    return {
        "mean": float(deltas.mean()),
        "ci95": np.quantile(sampled, [0.025, 0.975]).tolist(),
        "unit": "game",
        "resamples": 500,
    }


def reliability(rows, probabilities):
    labels, weights = np.array([r["label"] for r in rows]), game_weights(rows)
    curves = {}
    for label, name in enumerate(("white", "draw", "black")):
        points = []
        bins = np.minimum((probabilities[:, label] * 10).astype(int), 9)
        for index in range(10):
            mask = bins == index
            if mask.any():
                points.append(
                    {
                        "predicted": float(
                            np.average(probabilities[mask, label], weights=weights[mask])
                        ),
                        "observed": float(np.average(labels[mask] == label, weights=weights[mask])),
                        "positions": int(mask.sum()),
                        "game_weight": float(weights[mask].sum()),
                    }
                )
        curves[name] = points
    return curves


def evaluate(artifact, train, calibration, test):
    validate_splits(train, calibration, test)
    x = matrix(test)
    prediction = predict_matrix(artifact, x)
    model = HumanModel(artifact)
    # Check serialization against independently executed portable serving on real positions.
    for index in np.linspace(0, len(test) - 1, min(30, len(test)), dtype=int):
        row = test[index]
        board = chess.Board()
        for san in row["moves"]:
            board.push_san(san)
        served = model.predict(
            board, HumanContext(**row["context"]), row["evaluation_cp"], row.get("mate")
        )
        if not np.allclose(
            list(served.probabilities.model_dump().values()), prediction[index], atol=1e-10
        ):
            raise ValueError("Portable model differs from training inference")
    candidates = {
        "human": prediction,
        "uncalibrated": predict_matrix(artifact, x, 1),
        **baselines(train, test),
    }
    y, weights = [r["label"] for r in test], game_weights(test)
    scores = {name: metrics(y, p, weights) for name, p in candidates.items()}
    deltas = {
        name: bootstrap_delta(test, prediction, p)
        for name, p in candidates.items()
        if name not in ("human", "uncalibrated")
    }
    game_labels = {row["game_id"]: row["label"] for row in test}
    counts = np.bincount(list(game_labels.values()), minlength=3).tolist()
    gates = {
        "sample_size": len({r["game_id"] for r in train}) >= 1000
        and len({r["game_id"] for r in calibration}) >= 300
        and len(game_labels) >= 300,
        "test_class_support": min(counts) >= 20,
        "log_loss_beats_baselines": all(
            scores["human"]["log_loss"] < scores[name]["log_loss"] for name in deltas
        ),
        "brier_beats_baselines": all(
            scores["human"]["brier"] < scores[name]["brier"] for name in deltas
        ),
        "improvement_ci": all(delta["ci95"][1] < 0 for delta in deltas.values()),
        "top_label_ece": scores["human"]["top_label_ece"] <= 0.06,
    }
    slices = {}
    groups = {
        "blitz": [r["context"]["speed"] == "blitz" for r in test],
        "rapid": [r["context"]["speed"] == "rapid" for r in test],
    }
    for low, high in ((600, 1400), (1400, 1800), (1800, 2200), (2200, 3001)):
        groups[f"rating_{low}_{high - 1}"] = [
            low <= (r["context"]["white_rating"] + r["context"]["black_rating"]) / 2 < high
            for r in test
        ]
    for name, mask in groups.items():
        mask = np.asarray(mask)
        if not mask.any():
            continue
        selected = [r for r, keep in zip(test, mask, strict=True) if keep]
        slices[name] = {
            "games": len({r["game_id"] for r in selected}),
            "positions": len(selected),
            "scores": {
                candidate: metrics(np.asarray(y)[mask], p[mask], weights[mask])
                for candidate, p in candidates.items()
            },
        }
    return {
        "model_id": artifact["model_id"],
        "accepted_for_pilot": all(gates.values()),
        "gates": gates,
        "test_games": len(game_labels),
        "test_positions": len(test),
        "test_game_labels_white_draw_black": counts,
        "metrics": scores,
        "paired_log_loss_deltas": deltas,
        "slices": slices,
        "reliability": reliability(test, prediction),
        "weighting": (
            "equal per game; Brier is sum of squared errors over three classes (range 0..2)"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("train", "calibration", "test", "model", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    datasets, manifests = [], []
    for path in (args.train, args.calibration, args.test):
        manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest["dataset_sha256"]:
            raise ValueError(f"Dataset checksum mismatch: {path.name}")
        datasets.append([json.loads(line) for line in content.splitlines() if line])
        manifests.append(manifest)
    validate_splits(*datasets)
    print("Fitting training set; test outcomes are reserved for final evaluation.", flush=True)
    artifact = fit_model(datasets[0], datasets[1])
    print("Evaluating held-out month.", flush=True)
    report = evaluate(artifact, *datasets)
    artifact["approved_for_pilot"] = report["accepted_for_pilot"]
    artifact["metadata"].update(
        {
            "provenance": manifests,
            "evaluation": {
                key: report[key] for key in ("gates", "test_games", "test_positions", "metrics")
            },
        }
    )
    args.model.parent.mkdir(parents=True, exist_ok=True)
    args.report.mkdir(parents=True, exist_ok=True)
    args.model.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (args.report / "evaluation.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (args.report / "data-provenance.json").write_text(
        json.dumps(manifests, indent=2) + "\n", encoding="utf-8"
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 3, figsize=(11, 3.8), constrained_layout=True)
    for axis, (name, points) in zip(axes, report["reliability"].items(), strict=True):
        axis.plot([0, 1], [0, 1], "--", color="#888888", linewidth=1)
        axis.plot(
            [p["predicted"] for p in points], [p["observed"] for p in points], "o-", color="#2563a6"
        )
        axis.set(
            xlim=(0, 1),
            ylim=(0, 1),
            title=name.capitalize(),
            xlabel="Predicted probability",
            ylabel="Observed fraction",
        )
        axis.grid(alpha=0.2)
    figure.suptitle(f"Human outcome pilot — October 2026 holdout ({report['test_games']} games)")
    figure.savefig(args.report / "calibration.png", dpi=160)
    plt.close(figure)
    print(
        json.dumps(
            {key: report[key] for key in ("model_id", "accepted_for_pilot", "gates", "metrics")},
            indent=2,
        )
    )
    if not report["accepted_for_pilot"]:
        raise SystemExit("Candidate failed pilot criteria; runtime loading remains disabled.")


if __name__ == "__main__":
    main()
