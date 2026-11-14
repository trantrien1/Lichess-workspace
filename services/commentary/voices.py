"""The selectable commentary voices.

Lila's whitelist (app/controllers/Commentary.scala) and the round UI (ui/round/src/commentary.ts) list the
same ids; tests/test_commentary.py checks that the three lists agree.

Intelligibility, measured on 6 commentary lines transcribed by Whisper small (character error rate, with tones;
lower is better; about ±0.04 between runs): vais1000 0.08-0.11, pitch x0.88 0.09, x1.08 0.08; vivos speakers at
length 0.75: 28: 0.23, 45: 0.18, 40: 0.24 (at their own slower pace 0.20, 0.21, 0.18). The vivos model (Piper 0.2)
has no tone phonemes, so its voices blur tones; they are marked experimental.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    id: str
    # Piper model file name, without ".onnx".
    model: str
    speaker: int | None = None
    # Below 1 the audio is replayed slower: a lower, darker voice. The synthesis is sped up by the same
    # factor, so the sentence keeps its length. Above 1 the opposite.
    pitch: float = 1.0
    # Piper's length scale on top of the model's own: below 1 speaks faster.
    length: float = 1.0


VAIS1000 = "vi_VN-vais1000-medium"
VIVOS = "vi_VN-vivos-x_low"
CATALOGUE = (
    Voice("piper-vais1000", VAIS1000),
    Voice("piper-vais1000-low", VAIS1000, pitch=0.88),
    Voice("piper-vais1000-high", VAIS1000, pitch=1.08),
    # vivos speakers read about a third slower than vais1000; a comment must not lag behind the game.
    Voice("piper-vivos-28", VIVOS, speaker=28, length=0.75),
    Voice("piper-vivos-45", VIVOS, speaker=45, length=0.75),
    Voice("piper-vivos-40", VIVOS, speaker=40, length=0.75),
)
VOICES = {voice.id: voice for voice in CATALOGUE}
DEFAULT = CATALOGUE[0].id
# What a player hears when trying a voice.
SAMPLE = "Xin chào, đây là giọng bình luận bạn vừa chọn. Trắng vừa đưa mã lên f3."
