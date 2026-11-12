"""Stream a bounded prefix of one official archive into a reproducible pilot sample."""

import argparse
import hashlib
import io
import itertools
import json
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import zstandard

from .dataset import SAMPLE_PLIES, samples_from_pgn


class LimitedReader:
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit
        self.count = 0
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        remaining = self.limit - self.count
        data = (
            self.stream.read(min(remaining, size if size >= 0 else remaining)) if remaining else b""
        )
        self.count += len(data)
        self.digest.update(data)
        return data


def collect_stream(stream, output, *, byte_limit, game_limit):
    if byte_limit <= 0 or game_limit <= 0:
        raise ValueError("Collection limits must be positive")
    limited = LimitedReader(stream, byte_limit)
    decompressed = zstandard.ZstdDecompressor().stream_reader(limited, closefd=False)
    text = io.TextIOWrapper(decompressed, encoding="utf-8")
    seen, dates, labels = set(), [], [0, 0, 0]
    positions = 0
    digest = hashlib.sha256()
    try:
        for game_id, group in itertools.groupby(samples_from_pgn(text), lambda row: row["game_id"]):
            if game_id in seen:
                continue
            if len(seen) >= game_limit:
                break
            seen.add(game_id)
            for i, row in enumerate(group):
                line = json.dumps(row, separators=(",", ":")) + "\n"
                output.write(line)
                digest.update(line.encode())
                positions += 1
                if i == 0:
                    dates.append(row["played_at"])
                    labels[row["label"]] += 1
            if len(seen) % 500 == 0:
                print(
                    f"Collected {len(seen)} games / {positions} positions",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        text.close()
    return {
        "games": len(seen),
        "positions": positions,
        "game_labels_white_draw_black": labels,
        "first_game_at": min(dates, default=None),
        "last_game_at": max(dates, default=None),
        "compressed_bytes": limited.count,
        "compressed_prefix_sha256": limited.digest.hexdigest(),
        "dataset_sha256": digest.hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True)
    parser.add_argument("--games", type=int, default=2000)
    parser.add_argument("--max-mib", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", args.month):
        parser.error("Month must be YYYY-MM")
    if not 1 <= args.max_mib <= 512 or not 1 <= args.games <= 100_000:
        parser.error("Use 1..512 MiB and 1..100000 games")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("Output exists; use the saved dataset or choose a new output path")
    url = f"https://database.lichess.org/standard/lichess_db_standard_rated_{args.month}.pgn.zst"
    limit = args.max_mib * 1024 * 1024
    request = urllib.request.Request(url, headers={"Range": f"bytes=0-{limit - 1}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            report = collect_stream(response, output, byte_limit=limit, game_limit=args.games)
        report.update(
            {
                "source": url,
                "license": "CC0-1.0",
                "source_documentation": "https://database.lichess.org/#standard_games",
                "retrieved_at": datetime.now(UTC).isoformat(),
                "month": args.month,
                "sampling": "archive prefix; analyzed rated human blitz/rapid; fixed plies",
                "selected_plies": list(SAMPLE_PLIES),
                "extractor_version": "human-pgn-v2",
                "game_limit": args.games,
                "byte_limit": limit,
                "etag": response.headers.get("ETag"),
            }
        )
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
