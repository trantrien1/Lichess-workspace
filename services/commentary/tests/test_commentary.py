"""Run from services/commentary: python -m unittest discover -s tests"""

import asyncio
import base64
import io
import os
import random
import re
import sys
import tempfile
import time
import unicodedata
import unittest
import wave
from pathlib import Path

import chess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Recent, Settings, Worker  # noqa: E402
from core import Clock, Job, comment_id, extract, speech_text, vi_number  # noqa: E402
from narrative import GENERIC_MOTIFS, brief, compose  # noqa: E402
from phrases import COLOUR, FINISH, FINISH_CLOSERS, IDLE, INTRO, MOVE, TRANSITIONS, dress, fill, seed_of  # noqa: E402
from phrases import OPENING_CENTRAL, OPENING_FLEXIBLE, OPENING_GENERAL, OPENING_RARE  # noqa: E402
from voices import CATALOGUE, DEFAULT, VOICES  # noqa: E402
from insight import INSIGHT, choose  # noqa: E402


def job(sans, request="r:1", game="abcd1234", accepted_ago_ms=0, event="move", status="started", winner=None,
        **extra):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    now = int(time.time() * 1000)
    return Job(schemaVersion=1, requestId=request, gameId=game, gameRevision=1, ply=len(sans),
               initialFen=chess.STARTING_FEN, sanHistory=sans, fenAfter=board.fen(), status=status, winner=winner,
               expiresAt=now + 11000, acceptedAt=now - accepted_ago_ms, event=event, **extra)


ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "d3", "d6"]
SQUARE = re.compile(r"(?<!\w)[a-h][1-8](?!\w)")


def words(text):
    return " " + " ".join(re.findall(r"\w+", unicodedata.normalize("NFC", text).lower())) + " "


def extract_composed(sans, **extra):
    facts = extract(job(sans, **extra))
    facts.values["engine"] = {"unavailable": True}
    compose(facts, job(sans, **extra))
    return facts


class Idle(unittest.TestCase):
    def facts(self, sans, count=1, **extra):
        return extract(job(sans, event="idle", idleCount=count, **extra))

    def test_before_the_first_move_it_waits_for_white(self):
        facts = self.facts([], thinkingMs=10_000)
        self.assertEqual(facts.alternatives[0], "Trắng vẫn đang cân nhắc nước đi đầu tiên.")
        self.assertEqual(facts.values["narration"]["factIds"], ["idle"])

    def test_each_remark_in_a_long_think_takes_another_topic(self):
        themes = [self.facts(ITALIAN, count, clock=Clock(white=170, black=95)).values["narration"]["theme"]
                  for count in range(1, 5)]
        self.assertEqual(len(set(themes)), 4, themes)
        self.assertTrue(all(theme.startswith("idle_") for theme in themes))

    def test_board_facts_are_true(self):
        texts = {text for count in range(1, 8)
                 for text in self.facts(ITALIAN, count, clock=Clock(white=170, black=95)).alternatives}
        self.assertIn("Đồng hồ của Trắng còn 2 phút 50 giây.", texts)
        self.assertIn("Lực lượng hai bên vẫn đang cân bằng.", texts)
        self.assertIn("Vua Trắng đã nhập thành, còn vua Đen thì chưa.", texts)
        self.assertIn("Nước vừa rồi, Đen đưa tốt đến d6.", texts)
        self.assertIn("Trắng còn 2 phút 50 giây, còn Đen có 1 phút 35 giây.", texts)

    def test_a_low_clock_and_a_check_come_first(self):
        for count in (1, 2, 3):
            clock = self.facts(ITALIAN, count, clock=Clock(white=12, black=95))
            self.assertEqual(clock.alternatives[0], "Trắng chỉ còn 12 giây trên đồng hồ!")
        check = self.facts(["e4", "f5", "Qh5+"], 3)
        self.assertEqual(check.values["narration"]["theme"], "idle_check")
        self.assertIn("Đen đang bị chiếu", check.alternatives[0])

    def test_a_long_think_and_a_clock_gap_are_mentioned(self):
        texts = {text for count in range(1, 8)
                 for text in self.facts(ITALIAN, count, thinkingMs=47_000, clock=Clock(white=50, black=170)).alternatives}
        self.assertIn("Trắng đã suy nghĩ hơn 40 giây cho nước này.", texts)
        self.assertIn("Trắng đang ít thời gian hơn Đen khá nhiều.", texts)

    def test_no_engine_and_no_cached_voice_for_idle(self):
        analysed = []

        class Worker_(FakeWorker):
            async def analyse(self, facts):
                analysed.append(1)
                return {"unavailable": True}
        worker = Worker_(Settings())
        idle = job(["e4"], event="idle", idleCount=1, thinkingMs=12_000)
        self.assertIsNone(worker.cached_start(idle))
        result = run(worker.process(idle, connected))
        self.assertEqual((result["type"], result["kind"], result["factIds"]), ("comment", "idle", ["idle"]))
        self.assertEqual(analysed, [])
        again = run(worker.process(job(["e4"], "r:2", event="idle", idleCount=1, thinkingMs=12_000), connected))
        self.assertNotEqual(result["commentId"], again["commentId"])


class Motifs(unittest.TestCase):
    def theme(self, sans):
        facts = extract_composed(sans)
        return facts.values["narration"]["theme"], facts.alternatives

    def test_the_moment_decides_the_sentences(self):
        self.assertEqual(self.theme(ITALIAN[:7])[0], "castling")
        line = ["f4", "e6", "Kf2", "Qh4+"]
        self.assertEqual(self.theme(line)[0], "check")
        self.assertEqual(self.theme(line + ["Kf3", "Qh5+"])[0], "check_again")
        theme, king = self.theme(line + ["Kf3"])
        self.assertEqual(theme, "escape")
        self.assertIn("Vua Trắng tránh sang f3, thoát khỏi nước chiếu.", king)
        theme, block = self.theme(line + ["g3"])
        self.assertEqual(theme, "escape")
        self.assertIn("Trắng dùng tốt che chắn cho vua.", block)

    def test_first_moves_use_the_startup_audio_lines(self):
        startup = set(OPENING_CENTRAL + OPENING_FLEXIBLE + OPENING_GENERAL + OPENING_RARE)
        for first in ("e4", "Nf3", "a3"):
            self.assertTrue(set(extract_composed([first]).alternatives) <= startup, first)

    def test_a_capture_names_who_took_what(self):
        theme, texts = self.theme(["e4", "d5", "exd5"])
        self.assertEqual(theme, "capture")
        self.assertIn("Trắng vừa dùng tốt bắt tốt Đen.", texts)
        self.assertTrue(all("{" not in text for text in texts))


class Phrasebook(unittest.TestCase):
    MOVE_VALUES = {"side": "Trắng", "opponent": "Đen", "to_move": "Đen", "piece": "mã", "Piece": "Mã",
                   "captured": "tượng", "Captured": "Tượng", "target": "f3", "origin": "g1", "wing": "vua",
                   "castle": "ngắn", "promo": "hậu", "victim": "xe", "action": "Trắng vừa dùng mã bắt tượng Đen"}
    IDLE_VALUES = {"side": "Đen", "other": "Trắng", "seconds": 40, "time": "12 giây", "other_time": "1 phút",
                   "leader": "Trắng", "trailer": "Đen", "yes": "Trắng", "no": "Đen", "move": 12, "pieces": 20,
                   "mover": "Trắng", "victim": "Đen", "piece": "mã", "captured": "tốt", "target": "e5",
                   "wing": "vua", "castle": "ngắn", "promo": "hậu"}

    def check_sentence(self, text):
        self.assertEqual(text, unicodedata.normalize("NFC", text))
        self.assertNotIn("{", text)
        self.assertNotIn("  ", text)
        self.assertNotIn("None", text)
        self.assertIn(text[-1], ".!?", text)
        self.assertTrue(text[0].isupper(), text)

    def test_every_sentence_fills_and_reads_as_a_sentence(self):
        lines = [*INTRO, *TRANSITIONS, *OPENING_CENTRAL, *OPENING_FLEXIBLE, *OPENING_GENERAL, *OPENING_RARE,
                 *FINISH_CLOSERS]
        for templates in MOVE.values():
            lines += fill(templates, self.MOVE_VALUES)
        for templates in IDLE.values():
            lines += fill(templates, self.IDLE_VALUES)
        for templates in FINISH.values():
            lines += fill(templates, {"winner": "Trắng", "loser": "Đen", "side": "Đen"})
        for text in lines:
            self.check_sentence(text)
        self.assertGreater(len(set(lines)), 280)
        self.assertTrue(all(len(templates) >= 4 for key, templates in MOVE.items()
                            if not key.startswith(("endgame_", "underpromotion", "queen_promotion"))))

    def test_lead_ins_and_closing_lines_assert_nothing(self):
        judgement = ("sai", "hớ", "xuất sắc", "chính xác", "tuyệt", "thắng", "thua", "hòa", "nên", "phải", "giây",
                     "phút", "đồng hồ")
        allowed_pieces = {"danger": {"vua"}, "passed_pawn": {"tốt"}}
        for group, (leads, tails) in COLOUR.items():
            for line in leads + tails:
                self.check_sentence(line.replace("{to_move}", "Trắng"))
                found = words(line)
                self.assertNotRegex(line, r"\d", line)
                self.assertIsNone(SQUARE.search(line), line)
                self.assertNotIn(" trắng ", found, line)
                self.assertNotIn(" đen ", found, line)
                if "{to_move}" in line:
                    self.assertTrue(line.endswith("?"), line)
                for word in judgement:
                    self.assertNotIn(f" {word} ", found, (group, line))
                for piece in ("vua", "hậu", "xe", "tượng", "mã", "tốt"):
                    if piece not in allowed_pieces.get(group, ()):
                        self.assertNotIn(f" {piece} ", found, (group, line))
            for line in leads:
                self.assertLessEqual(len(line), 40, line)

    def test_dress_keeps_the_sentence_and_is_repeatable(self):
        base = "Trắng phát triển mã lên f3."
        texts = {dress("development", base, "Đen", seed_of(i)) for i in range(2000)}
        self.assertIn(base, texts)
        exchange = "Trắng ăn lại ngay tại f3, hai bên trao đổi quân."
        self.assertFalse(any("được trao đổi" in dress("exchange", exchange, "Đen", seed_of(i)) for i in range(500)))
        check = "Một nước chiếu từ Trắng, vua Đen không được yên."
        self.assertFalse(any(dress("check", check, "Đen", seed_of(i)).startswith("Một pha")
                             for i in range(500)))
        self.assertTrue(all(base in text for text in texts))
        self.assertTrue(any(text.startswith(base) and text != base for text in texts), "closing lines")
        # A quiet move gets no filler lead-in ("Xem nào."); a capture may get one.
        self.assertFalse(any(text.endswith(base) and text != base for text in texts), "quiet lead-ins")
        capture = "Trắng dùng mã bắt tốt Đen tại e5."
        captures = {dress("capture", capture, "Đen", seed_of(i)) for i in range(500)}
        self.assertTrue(any(text.endswith(capture) and text != capture for text in captures), "lead-ins")
        self.assertGreater(len(texts), 2)
        self.assertEqual(dress("development", base, "Đen", 1234), dress("development", base, "Đen", 1234))
        self.assertTrue(any("Liệu Đen" in text for text in captures))
        self.assertFalse(any("Liệu Trắng" in text for text in texts | captures))

    def test_dress_avoids_what_was_just_heard(self):
        leads, tails = COLOUR["development"]
        heard = [" ".join(leads + [tail.format(to_move="Đen") for tail in tails])]
        base = "Trắng phát triển mã lên f3."
        self.assertEqual({dress("development", base, "Đen", seed_of(i), heard) for i in range(500)}, {base})

    def test_no_question_after_a_question_and_no_lead_before_an_exclamation(self):
        question = "Liệu Trắng có ý đồ gì trong nước đi này?"
        for seed in map(seed_of, range(500)):
            tail = dress("intention", question, "Đen", seed).split(question)[1]
            self.assertFalse(tail.endswith("?"), tail)
            self.assertTrue(dress("check", "Chiếu! Đen phải chống chiếu.", "Đen", seed).startswith("Chiếu!"))


class Finish(unittest.TestCase):
    FOOLS_MATE = ["f3", "e5", "g4", "Qh4#"]

    def test_the_last_move_and_the_game_over_notice_say_the_same(self):
        move = job(self.FOOLS_MATE, "r:1")
        over = job(self.FOOLS_MATE, "r:2", status="mate", winner="black")
        first, second = extract(move), extract(over)
        self.assertEqual(first.alternatives, second.alternatives)
        self.assertEqual(comment_id(move, first), comment_id(over, second))
        self.assertIn("Đen", first.alternatives[0])

    def test_endings_name_the_right_sides(self):
        resign = extract(job(ITALIAN, status="resign", winner="white")).alternatives[0]
        self.assertTrue(resign.startswith(("Đen", "Ván cờ kết thúc, Đen")), resign)
        self.assertIn("Trắng", resign)
        flag = extract(job(ITALIAN, status="outoftime", winner="black")).alternatives[0]
        self.assertIn("Trắng", flag)
        repeat = ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1", "Ng8"]
        draw = extract(job(repeat, status="draw")).alternatives[0]
        self.assertTrue(any(draw.startswith(line.split(" ")[0]) for line in FINISH["repetition"]), draw)
        self.assertIn("hòa", draw.lower())
        self.assertIn(extract(job(["e4"], status="aborted")).alternatives[0], FINISH["aborted"])

    def test_endings_differ_between_games(self):
        texts = {extract(job(self.FOOLS_MATE, game=f"game{i:04d}")).alternatives[0] for i in range(40)}
        self.assertGreaterEqual(len(texts), 6)


class RandomGames(unittest.TestCase):
    def test_every_position_of_random_games_gets_a_filled_sentence(self):
        for number in range(6):
            rng = random.Random(number)
            board, sans = chess.Board(), []
            while not board.is_game_over() and len(sans) < 120:
                move = rng.choice(list(board.legal_moves))
                sans.append(board.san(move))
                board.push(move)
                for facts in (extract_composed(sans),
                              extract(job(sans, event="idle", idleCount=len(sans) % 4 + 1,
                                          thinkingMs=rng.randrange(60_000),
                                          clock=Clock(white=rng.randrange(300), black=rng.randrange(300))))):
                    self.assertTrue(facts.alternatives, sans)
                    for text in facts.alternatives:
                        self.assertNotIn("{", text)
                        self.assertNotIn("None", text)
                        self.assertIn(text[-1], ".!?", text)
                    theme = facts.values.get("narration", {}).get("theme")
                    to_move = "Trắng" if facts.board.turn else "Đen"
                    self.assertIn(facts.alternatives[0], dress(theme, facts.alternatives[0], to_move, len(sans)))


class SpeechText(unittest.TestCase):
    def test_squares_are_written_for_the_eye_and_spelled_for_the_voice(self):
        facts = extract(job(["Nf3"]))
        self.assertIn("g1", facts.alternatives[0])
        self.assertIn("f3", facts.alternatives[0])
        self.assertNotIn("ép", facts.alternatives[0])
        spoken = speech_text(facts.alternatives[0])
        self.assertIn("gờ một", spoken)
        self.assertIn("ép ba", spoken)

    def test_only_whole_square_names_are_spelled(self):
        self.assertEqual(speech_text("Tốt e4, xe h8."), "Tốt e bốn, xe hát tám.")
        self.assertEqual(speech_text("Đen a1b2 h9 e44"), "Đen a1b2 h9 e44")
        self.assertEqual(speech_text("Không có ô nào."), "Không có ô nào.")

    def test_files_numbers_and_names_are_read_out(self):
        self.assertEqual(speech_text("Hai xe trên cột c."), "Hai xe trên cột xê.")
        self.assertEqual(speech_text("Còn 21 giây, nước thứ 15."), "Còn hai mươi mốt giây, nước thứ mười lăm.")
        self.assertEqual(speech_text("Đã hơn 105 giây."), "Đã hơn một trăm lẻ năm giây.")
        self.assertEqual(speech_text("Đây là phòng thủ Sicilia."), "Đây là phòng thủ Xi xi li a.")
        self.assertEqual([vi_number(n) for n in (0, 10, 14, 24, 55, 100, 1005, 2020)],
                         ["không", "mười", "mười bốn", "hai mươi tư", "năm mươi lăm", "một trăm",
                          "một nghìn không trăm lẻ năm", "hai nghìn không trăm hai mươi"])


class AntiRepetition(unittest.TestCase):
    def test_unsaid_sentences_first_then_longest_ago(self):
        recent = Recent(window=3)
        self.assertEqual(recent.order("g", ["a", "b", "c"]), ["a", "b", "c"])
        recent.record("g", "a")
        self.assertEqual(recent.order("g", ["a", "b", "c"]), ["b", "c", "a"])
        recent.record("g", "b")
        recent.record("g", "c")
        self.assertEqual(recent.order("g", ["c", "b", "a"]), ["a", "b", "c"])
        self.assertEqual(recent.order("other", ["c", "b"]), ["c", "b"])

    def test_the_same_template_with_other_names_counts_as_said(self):
        recent = Recent()
        recent.record("g", "Liệu Trắng đang ấp ủ ý tưởng gì đây?")
        options = ["Liệu Đen đang ấp ủ ý tưởng gì đây?", "Đen đang hướng tới kế hoạch nào với nước vừa rồi?"]
        self.assertEqual(recent.order("g", options), options[::-1])
        recent.record("g", "Trắng triển khai mã tại f3.")
        self.assertEqual(recent.order("g", ["Đen triển khai tượng tại c5.", "Tượng Đen đã nhập cuộc!"])[0],
                         "Tượng Đen đã nhập cuộc!")

    def test_memory_is_bounded(self):
        recent = Recent(games=2, window=2)
        for game in "xyz":
            recent.record(game, "t", "m")
        self.assertEqual(list(recent.games), ["y", "z"])
        for text in "123":
            recent.record("z", text)
        self.assertEqual([t for t, _ in recent.said("z")], ["2", "3"])

    def test_a_generic_motif_is_not_repeated_back_to_back(self):
        for sans in (["e4", "e5", "Nf3", "Nc6", "d4"], ["d4", "d5", "c4", "e6", "Nc3", "c6", "e4"],
                     ["e4", "e5", "d3"], ["e4", "c5", "c3"]):
            facts = extract_composed(sans)
            motif = facts.values["narration"]["theme"]
            if motif not in GENERIC_MOTIFS:
                continue
            again = extract(job(sans))
            again.values["engine"] = {"unavailable": True}
            compose(again, job(sans), [motif])
            self.assertEqual(again.values["narration"]["theme"], "event")
            self.assertTrue(any(facts.values["move"]["to"] in text for text in again.alternatives))
            return
        self.fail("no sample produced a generic motif")


class FakeWorker(Worker):
    def __init__(self, settings):
        super().__init__(settings)
        self.spoken = []

    async def analyse(self, facts):
        return {"unavailable": True}

    async def synthesize(self, text, voice):
        self.spoken.append(text)
        return {"mime": "audio/wav", "base64": "", "provider": "test", "voice": voice}


async def connected():
    return False


def run(coroutine):
    return asyncio.run(coroutine)


class Process(unittest.TestCase):
    def test_payload_has_written_text_spoken_text_and_no_engine_facts(self):
        worker = FakeWorker(Settings())
        result = run(worker.process(job(["Nf3", "Nf6", "Nc3"]), connected))
        self.assertEqual(result["type"], "comment")
        self.assertNotIn("facts", result)
        self.assertNotIn("ép", result["text"])
        self.assertEqual(result["speechText"], speech_text(result["text"]))
        self.assertEqual(worker.spoken, [result["speechText"]])
        for key in ("generator", "fallback"):
            self.assertNotIn(key, result)
        self.assertEqual(set(result["timings"]), {"engineMs", "composedMs", "audioReadyMs"})

    def test_debug_flag_keeps_facts_inside_the_service_payload(self):
        worker = FakeWorker(Settings(debug_facts=True))
        self.assertIn("facts", run(worker.process(job(["e4", "e5", "Nf3"]), connected)))

    def test_the_same_position_twice_gets_a_different_sentence(self):
        worker = FakeWorker(Settings())
        first = run(worker.process(job(["e4", "e5", "Nf3"], "r:1"), connected))
        second = run(worker.process(job(["e4", "e5", "Nf3"], "r:2"), connected))
        self.assertNotEqual(first["text"], second["text"])

    def test_the_sentence_and_the_whole_line_are_remembered(self):
        worker = FakeWorker(Settings())
        texts = []
        for i, sans in enumerate((ITALIAN[:3], ITALIAN[:4], ITALIAN[:5], ITALIAN[:6], ITALIAN[:7], ITALIAN[:8])):
            result = run(worker.process(job(sans, f"r:{i}"), connected))
            base = worker.recent.said("abcd1234")[-1][0]
            self.assertIn(base, result["text"])
            self.assertEqual(worker.recent.spoken("abcd1234")[-1], result["text"])
            texts.append(result["text"])
        self.assertEqual(len(set(texts)), len(texts))

    def test_a_voice_whose_model_is_missing_speaks_with_the_default(self):
        worker = FakeWorker(Settings())
        result = run(worker.process(job(["e4", "e5", "Nf3"], voice="piper-vivos-28"), connected))
        self.assertEqual(result["audio"]["voice"], DEFAULT)


class Voices(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]

    def test_lila_and_the_round_ui_offer_exactly_the_catalogue(self):
        for path in ("lila/app/controllers/Commentary.scala", "lila/ui/round/src/commentary.ts"):
            file = self.ROOT / path
            if not file.is_file():
                self.skipTest("lila is not next to the service")
            ids = set(re.findall(r"""["'](piper-[a-z0-9-]+)["']""", file.read_text(encoding="utf-8")))
            self.assertEqual(ids, set(VOICES), path)

    def test_an_unknown_voice_is_refused(self):
        with self.assertRaises(ValueError):
            extract(job(["e4"], voice="piper-nope"))

    def test_models_are_found_next_to_the_default_one(self):
        worker = Worker(Settings(piper_model="/m/custom.onnx"))
        self.assertEqual(worker.model_path(VOICES[DEFAULT]), Path("/m/custom.onnx"))
        # Pitch variants share the default voice's model, hence its Piper process.
        self.assertEqual(worker.model_path(VOICES["piper-vais1000-low"]), Path("/m/custom.onnx"))
        self.assertEqual(worker.model_path(VOICES["piper-vivos-28"]), Path("/m/vi_VN-vivos-x_low.onnx"))
        worker = Worker(Settings(piper_model="/m/custom.onnx", piper_dir="/v"))
        self.assertEqual(worker.model_path(VOICES["piper-vivos-28"]), Path("/v/vi_VN-vivos-x_low.onnx"))
        self.assertIsNone(Worker(Settings(piper_model="", piper_dir="")).model_path(VOICES[DEFAULT]))

    def test_only_installed_voices_are_offered_and_others_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("vi_VN-vais1000-medium.onnx", "vi_VN-vais1000-medium.onnx.json", "vi_VN-vivos-x_low.onnx"):
                (Path(folder) / name).touch()
            worker = Worker(Settings(piper_model=f"{folder}/vi_VN-vais1000-medium.onnx"))
            worker.find_voices()
        # vivos has no .json next to it, so it is not installed.
        self.assertEqual(worker.voices, ["piper-vais1000", "piper-vais1000-low", "piper-vais1000-high"])
        self.assertEqual(worker.voice_for("piper-vais1000-low"), "piper-vais1000-low")
        self.assertEqual(worker.voice_for("piper-vivos-28"), DEFAULT)


class PiperVoices(unittest.TestCase):
    """Real synthesis; skipped unless every model is installed (BLV_PIPER_DIR or ~/.cache/piper)."""

    def test_every_voice_speaks_and_a_pitch_change_keeps_the_length(self):
        folder = os.getenv("BLV_PIPER_DIR") or str(Path.home() / ".cache/piper")
        worker = Worker(Settings(piper_model="", piper_dir=folder))
        worker.find_voices()
        if len(worker.voices) < len(CATALOGUE):
            self.skipTest("voice models not installed")

        async def speak():
            try:
                return {voice: await worker.voice_audio("Đen nhập thành cánh vua.", voice) for voice in worker.voices}
            finally:
                await worker.close()
        audio = run(speak())
        self.assertEqual(len(worker.pipers), 2)

        def wav(voice):
            self.assertEqual(audio[voice]["voice"], voice)
            with wave.open(io.BytesIO(base64.b64decode(audio[voice]["base64"]))) as file:
                return file.getframerate(), file.getnframes() / file.getframerate()
        rate, seconds = wav(DEFAULT)
        for voice in ("piper-vais1000-low", "piper-vais1000-high"):
            shifted_rate, shifted_seconds = wav(voice)
            self.assertEqual(shifted_rate, round(rate * VOICES[voice].pitch))
            self.assertAlmostEqual(shifted_seconds, seconds, delta=seconds * .25)
        self.assertEqual({wav(voice)[0] for voice in worker.voices if "vivos" in voice}, {16000})


if __name__ == "__main__":
    unittest.main()


OPERA = ("e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5 Nxb5 cxb5 Bxb5+ Nbd7 O-O-O Rd8 "
         "Rxd7 Rxd7 Rd1 Qe6 Bxd7+ Nxd7 Qb8+").split()


class Insights(unittest.TestCase):
    """The commentator's reading of a move: board facts, hedged, never a move to play."""

    def reading(self, sans, fast=False, recent=()):
        facts = extract(job(sans))
        facts.values["engine"] = {"unavailable": True}
        compose(facts, job(sans), recent_insights=recent, fast=fast)
        return facts.insight

    def test_a_fast_game_hears_the_move_in_a_few_words(self):
        def said(sans):
            return brief(extract(job(sans)))
        self.assertEqual(said(OPERA[:3]), "Mã Trắng tới f3.")
        self.assertEqual(said(OPERA[:21]), "Tượng Trắng ăn tốt b5, chiếu!")
        self.assertEqual(said(OPERA[:23]), "Trắng nhập thành dài.")
        self.assertTrue(all(len(said(OPERA[:n])) <= 32 for n in range(2, len(OPERA) + 1)))

    def test_an_opening_is_named_at_the_move_that_reaches_it(self):
        insight = self.reading(["e4", "c5"])
        self.assertEqual(insight.key, "opening")
        self.assertTrue(all("phòng thủ Sicilia" in text for text in insight.texts))
        self.assertNotEqual(getattr(self.reading(["e4", "c5", "Nf3"]), "key", None), "opening")

    def test_a_pin_on_the_queen_and_a_new_threat_are_read_from_the_board(self):
        pin = self.reading(OPERA[:6])  # ...Bg4: the knight on f3 stands in front of the queen
        self.assertEqual(pin.key, "pin_queen")
        self.assertTrue(all("f3" in text for text in pin.texts))
        threat = self.reading(OPERA[:18])  # ...b5 attacks the bishop on c4
        self.assertEqual((threat.key, threat.subject), ("threat", str(chess.C4)))

    def test_a_piece_left_where_it_can_be_taken_is_a_question_not_a_verdict(self):
        insight = self.reading(OPERA[:19])  # Nxb5, the pawn on c6 can take back
        self.assertEqual(insight.key, "hanging")
        self.assertTrue(all("?" in text for text in insight.texts))

    def test_a_piece_that_is_simply_taken_back_forks_nothing(self):
        sans = "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O Nf3 h6 Bh4 b6 cxd5 Nxd5 Bxe7".split()
        self.assertNotIn(getattr(self.reading(sans), "key", None), ("fork", "threat"))

    def test_the_same_reading_is_not_repeated_and_fast_games_keep_only_striking_ones(self):
        pin = self.reading(OPERA[:6])
        self.assertIsNone(self.reading(OPERA[:6], recent=[f"{pin.key}:{pin.subject}"]))
        self.assertIsNone(self.reading(OPERA[:6], fast=True))  # a pin on the queen waits for a slower game
        self.assertIsNone(self.reading(["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "O-O", "Be7", "d3", "O-O", "h3"],
                                       fast=True))

    def test_engine_led_questions_stand_alone(self):
        found = [type("I", (), {"key": "threat", "subject": "1", "priority": 70, "texts": ["x"]})()]
        for motif in ("danger", "intention", "capture_question"):
            self.assertIsNone(choose(found, motif))

    def test_readings_name_no_engine_line_or_verdict(self):
        banned = ("thắng", "thua", "chiếu hết", "nên đi", "nước tốt nhất", "lợi thế")
        for key, texts in INSIGHT.items():
            self.assertGreaterEqual(len(texts), 3, key)
            for text in texts:
                self.assertFalse(any(word in text.lower() for word in banned), text)

    def test_every_reading_of_random_games_is_filled(self):
        rng = random.Random(7)
        for _ in range(40):
            board, sans = chess.Board(), []
            while not board.is_game_over() and len(sans) < 60:
                move = rng.choice(list(board.legal_moves))
                sans.append(board.san(move))
                board.push(move)
                if board.is_game_over():
                    break
                insight = self.reading(list(sans))
                if insight:
                    for text in insight.texts:
                        self.assertNotIn("{", text)


class IdleBoard(unittest.TestCase):
    def test_the_first_remark_reads_the_board_and_the_second_the_clock(self):
        sans = "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 e5 Nb3 Be6 f3 Be7 Qd2 O-O O-O-O Nbd7".split()
        clock = Clock(white=300, black=280)
        first = extract(job(sans, event="idle", idleCount=1, thinkingMs=25_000, clock=clock))
        second = extract(job(sans, event="idle", idleCount=2, thinkingMs=55_000, clock=clock))
        self.assertEqual(first.values["narration"]["theme"], "idle_position")
        self.assertIn("hai cánh khác nhau", first.alternatives[0])
        self.assertEqual(second.values["narration"]["theme"], "idle_clock")
