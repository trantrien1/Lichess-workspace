"""Bounded real-HTTP probe in an isolated prediction container; no production game mutations."""

import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    base = "http://127.0.0.1:8001"
    environment = {
        **os.environ,
        "ENGINE_WORKERS": str(args.workers),
        "ENGINE_TIME_MS": "250",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
            "--no-access-log",
            "--log-level",
            "error",
        ],
        env=environment,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )

    def health():
        with urllib.request.urlopen(base + "/health", timeout=3) as response:
            return json.load(response)

    def post(payload):
        request = urllib.request.Request(
            base + "/v1/predictions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def payload(game_id):
        return {
            "game_id": game_id,
            "moves": ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6"],
            "context": {
                "white_rating": 1500,
                "black_rating": 1700,
                "white_clock": 580,
                "black_clock": 590,
                "initial_seconds": 600,
                "increment_seconds": 5,
                "speed": "rapid",
            },
        }

    def wait_idle():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = health()
            if state["pending"] == state["active"] == 0:
                return state
            time.sleep(0.05)
        raise AssertionError("Worker did not drain within ten seconds")

    def valid(prediction):
        assert prediction["status"] == prediction["human"]["status"] == "ready", (
            prediction
        )
        for p in (prediction["probabilities"], prediction["human"]["probabilities"]):
            assert all(math.isfinite(v) and 0 <= v <= 1 for v in p.values()), p
            assert math.isclose(sum(p.values()), 1), p

    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                state = health()
                break
            except (OSError, urllib.error.URLError):
                assert process.poll() is None, "Probe server exited during startup"
                assert time.monotonic() < deadline, "Probe startup timed out"
                time.sleep(0.1)
        assert state["human_model"]["loaded"], state
        # Warm each engine before measuring the four-game burst.
        for i in range(args.workers):
            post(payload(f"warm{i:04d}"))
        wait_idle()
        before = health()
        games = [payload(f"load{i:04d}") for i in range(4)]
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=20) as pool:
            responses = list(pool.map(post, games * 25))
        assert all(r["status"] in ("pending", "ready") for r in responses)
        pending = {p["game_id"]: p for p in games}
        latencies = []
        while pending:
            assert time.monotonic() - started < 10, "Four-game burst did not finish"
            for game_id, p in list(pending.items()):
                result = post(p)
                if result["status"] == "ready":
                    valid(result)
                    latencies.append(round((time.monotonic() - started) * 1000, 1))
                    del pending[game_id]
                else:
                    assert result["status"] == "pending", result
            if pending:
                time.sleep(0.05)
        after = wait_idle()
        assert after["completed"] - before["completed"] == 4, after
        with ThreadPoolExecutor(max_workers=20) as pool:
            for result in pool.map(post, games * 25):
                valid(result)
        assert health()["completed"] == after["completed"], (
            "Cached spectators added engine work"
        )

        # Saturate the pending queue, then prove official results bypass and drain it.
        overload = [payload(f"busy{i:04d}") for i in range(96)]
        with ThreadPoolExecutor(max_workers=20) as pool:
            overloaded = list(pool.map(post, overload))
        saturated = health()
        busy = sum(r["status"] == "busy" for r in overloaded)
        assert busy > 0, "The bounded queue was not exercised"
        assert saturated["pending"] <= 64 and saturated["active"] <= args.workers, (
            saturated
        )
        with ThreadPoolExecutor(max_workers=20) as pool:
            aborted = list(
                pool.map(post, [{**p, "result": "aborted"} for p in overload])
            )
        assert all(
            r["status"] == "aborted" and "probabilities" not in r for r in aborted
        )
        final = wait_idle()
        assert final["errors"] == final["human_model"]["errors"] == 0, final
        print(
            json.dumps(
                {
                    "workers": args.workers,
                    "search_ms": 250,
                    "threads_per_engine": 1,
                    "model_id": final["human_model"]["model_id"],
                    "conditions": (
                        "isolated worker; engines warmed; four positions; "
                        "100 requests in 20 threads; 50ms unique-game polling"
                    ),
                    "games": 4,
                    "spectators": 100,
                    "game_ready_latency_ms": latencies,
                    "game_ready_p95_ms": sorted(latencies)[-1],
                    "engine_evaluations": after["completed"] - before["completed"],
                    "extra_evaluations_for_cached_requests": 0,
                    "saturation_games": 96,
                    "busy_responses": busy,
                    "queue_at_saturation": saturated,
                    "official_aborts": len(aborted),
                    "final": final,
                    "limitations": (
                        "One burst per configuration; "
                        "not a long-running capacity or gameplay benchmark"
                    ),
                },
                indent=2,
            )
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
