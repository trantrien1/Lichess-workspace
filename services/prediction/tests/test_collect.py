import io
import json

import zstandard
from test_dataset import PGN

from training.collect import collect_stream


def test_collection_deduplicates_games_and_honors_game_limit():
    other = PGN.replace("abcd1234", "wxyz5678")
    compressed = zstandard.ZstdCompressor().compress((PGN + PGN + other).encode())
    output = io.StringIO()
    report = collect_stream(io.BytesIO(compressed), output, byte_limit=1024 * 1024, game_limit=2)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert {row["game_id"] for row in rows} == {"abcd1234", "wxyz5678"}
    assert len(rows) == report["positions"] == 2
    assert report["games"] == 2


def test_compressed_byte_budget_is_enforced_even_if_server_ignores_range():
    compressed = zstandard.ZstdCompressor().compress(PGN.encode())
    source = io.BytesIO(compressed)
    output = io.StringIO()
    report = collect_stream(source, output, byte_limit=40, game_limit=100)
    assert source.tell() <= 40
    assert report["compressed_bytes"] <= 40
    assert report["games"] == 0
    assert output.getvalue() == ""
