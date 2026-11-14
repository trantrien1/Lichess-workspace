"""Exercise the running worker over HTTP, including deduplication and full-history results."""

import argparse
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://prediction:8000")
    args = parser.parse_args()

    context = {
        "white_rating": 1500,
        "black_rating": 1800,
        "white_clock": 300,
        "black_clock": 90,
        "initial_seconds": 600,
        "increment_seconds": 5,
        "speed": "rapid",
    }

    def predict(moves, result=None, human_context=context):
        payload = {"game_id": "smokeWDL", "moves": moves}
        if human_context is not None:
            payload["context"] = human_context
        if result:
            payload["result"] = result
        request = urllib.request.Request(
            args.url + "/v1/predictions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    def stats():
        with urllib.request.urlopen(args.url + "/health", timeout=3) as response:
            return json.load(response)

    moves = [
        "e4",
        "e5",
        "Nf3",
        "Nc6",
        "Bb5",
        "a6",
        "Ba4",
        "Nf6",
        "O-O",
        "Be7",
        "Re1",
        "b5",
        "Bb3",
        "d6",
        "c3",
        "O-O",
        "h3",
        "Nb8",
        "d4",
        "Nbd7",
    ]
    before = stats()
    assert before["human_model"]["loaded"], before
    latencies = []
    engine = ""
    for ply in range(len(moves) + 1):
        start = time.monotonic()
        while True:
            response = predict(moves[:ply])
            if response["status"] == "ready":
                break
            assert response["status"] == "pending", response
            assert time.monotonic() - start < 5, response
            time.sleep(0.05)
        latencies.append(round((time.monotonic() - start) * 1000, 1))
        probabilities = list(response["probabilities"].values())
        assert all(math.isfinite(p) and 0 <= p <= 1 for p in probabilities)
        assert math.isclose(sum(probabilities), 1)
        assert response["ply"] == ply
        if ply >= 4:
            assert response["human"]["status"] == "ready", response
            human = list(response["human"]["probabilities"].values())
            assert all(math.isfinite(p) and 0 <= p <= 1 for p in human)
            assert math.isclose(sum(human), 1)
        engine = response["engine"]

    completed = stats()["completed"]
    with ThreadPoolExecutor(max_workers=10) as pool:
        duplicate_results = list(pool.map(lambda _: predict(moves), range(100)))
    assert all(r["status"] == "ready" for r in duplicate_results)
    assert stats()["completed"] == completed, "Duplicate spectators triggered extra engine work"
    changed_context = predict(
        moves, human_context={**context, "white_rating": 2200, "white_clock": 5}
    )
    assert changed_context["human"]["probabilities"] != response["human"]["probabilities"]
    assert changed_context["probabilities"] == response["probabilities"]
    assert stats()["completed"] == completed, "Changing context must reuse the engine evaluation"
    assert predict(moves, human_context=None)["human"]["reason"] == "missing_context"

    # A takeback reuses its own result, while official results remain available immediately.
    takeback = predict(moves[:5])
    assert takeback["status"] == "ready" and takeback["ply"] == 5
    assert takeback["position_key"] != response["position_key"]
    for result in ["white", "draw", "black"]:
        finished = predict(moves, result)
        assert finished["source"] == "official"
        assert finished["probabilities"][result] == 1
        assert "human" not in finished
    assert "probabilities" not in predict(moves, "aborted")
    report = {
        "engine": engine,
        "positions": len(latencies),
        "http_poll_ms": 50,
        "worker_http_p95_ms": sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1],
        "cold_position_ms": latencies[0],
        "latencies_ms": latencies,
        "duplicate_spectators": 100,
        "extra_evaluations_for_duplicates": stats()["completed"] - completed,
        "human_context_change_reuses_engine": True,
        "anonymous_human_prediction_withheld": True,
        "stats_before": before,
        "stats_after": stats(),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
