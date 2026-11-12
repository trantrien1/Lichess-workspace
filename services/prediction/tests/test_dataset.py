import io

from training.dataset import samples_from_pgn

PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/abcd1234"]
[UTCDate "2026.08.01"]
[UTCTime "12:00:00"]
[WhiteElo "1500"]
[BlackElo "1700"]
[WhiteRatingDiff "9"]
[BlackRatingDiff "-9"]
[TimeControl "300+2"]
[Result "1-0"]
[Termination "Normal"]

1. e4 { [%eval 0.20] [%clk 0:05:00] } e5 { [%eval 0.10] [%clk 0:05:00] }
2. Nf3 { [%eval 0.35] [%clk 0:04:57] } Nc6 { [%eval -0.75] [%clk 0:04:55] }
3. Bb5 { [%eval 1.20] [%clk 0:04:40] } a6 { [%eval 2.50] [%clk 0:04:30] } 1-0

"""


def test_snapshot_uses_only_clocks_and_white_evaluation_known_at_that_ply():
    # Reading the final clocks or using side-to-move CP would break these literal values.
    rows = list(samples_from_pgn(io.StringIO(PGN), selected_plies=(3, 4)))
    assert len(rows) == 2
    assert rows[0]["evaluation_cp"] == 35
    assert rows[1]["evaluation_cp"] == -75
    assert rows[1]["context"] == {
        "white_rating": 1500,
        "black_rating": 1700,
        "white_clock": 297.0,
        "black_clock": 295.0,
        "initial_seconds": 300,
        "increment_seconds": 2,
        "speed": "blitz",
    }
    assert rows[1]["moves"] == ["e4", "e5", "Nf3", "Nc6"]
    assert rows[1]["label"] == 0
    assert rows[1]["played_at"] == "2026-08-01T12:00:00+00:00"


def test_changing_result_rating_deltas_termination_and_future_does_not_change_inputs():
    changed = PGN.replace('"1-0"', '"0-1"').replace('"9"', '"-100"')
    changed = changed.replace('"Normal"', '"Time forfeit"')
    changed = changed.replace("[%eval 2.50]", "[%eval -20.00]").replace("0:04:30", "0:00:01")
    changed = changed.replace("} 1-0", "} 0-1")
    original = next(samples_from_pgn(io.StringIO(PGN)))
    alternative = next(samples_from_pgn(io.StringIO(changed)))
    assert original.pop("label") == 0
    assert alternative.pop("label") == 2
    assert original == alternative


def test_incomplete_last_game_is_not_treated_as_a_labelled_game():
    truncated = PGN[: PGN.index("3. Bb5")]
    assert list(samples_from_pgn(io.StringIO(truncated))) == []
    assert len(list(samples_from_pgn(io.StringIO(PGN + truncated)))) == 1


def test_current_game_url_and_legacy_url_identify_the_same_game():
    current = PGN.replace("https://lichess.org/abcd1234", "https://lichess.org/game/abcd1234")
    assert len(list(samples_from_pgn(io.StringIO(current)))) == 1
    rows = list(samples_from_pgn(io.StringIO(current + PGN)))
    assert len(rows) == 1
    assert rows[0]["game_id"] == "abcd1234"


def test_missing_current_clock_is_not_filled_from_future_moves():
    missing = PGN.replace("[%clk 0:04:57]", "")
    assert list(samples_from_pgn(io.StringIO(missing))) == []


def test_bot_variant_invalid_move_and_missing_ratings_are_excluded():
    invalid = [
        PGN.replace('[WhiteElo "1500"]', '[WhiteTitle "BOT"]\n[WhiteElo "1500"]'),
        PGN.replace('[WhiteElo "1500"]', '[Variant "Chess960"]\n[WhiteElo "1500"]'),
        PGN.replace('"1500"', '"?"'),
        PGN.replace("2. Nf3", "2. --"),
        PGN.replace("Rated Blitz", "Rated Bullet"),
    ]
    for text in invalid:
        assert list(samples_from_pgn(io.StringIO(text))) == []


def test_default_sampling_covers_both_sides_to_move():
    import chess

    moves = "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6".split()
    body = " ".join(f"{san} {{ [%eval 0.2] [%clk 0:04:00] }}" for san in moves) + " 1-0\n\n"
    text = PGN.split("\n\n", 1)[0] + "\n\n" + body
    rows = list(samples_from_pgn(io.StringIO(text)))
    assert {chess.Board(row["fen"]).turn for row in rows} == {chess.WHITE, chess.BLACK}
