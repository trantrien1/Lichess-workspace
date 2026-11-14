"""Killable, one-thread offline TTS subprocess; stdout contains WAV only.

Server mode reads JSON lines {"text", "speaker"?, "pitch"?, "length"?} and answers {"audio": base64 WAV}.
"""

import io
import base64
import json
import sys
import wave

import numpy
import onnxruntime
from piper import PiperVoice, SynthesisConfig
from piper.config import PiperConfig

model = sys.argv[1]
options = onnxruntime.SessionOptions()
options.intra_op_num_threads = 1
options.inter_op_num_threads = 1
session = onnxruntime.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])
with open(model + ".json", encoding="utf-8") as config:
    voice = PiperVoice(session, PiperConfig.from_dict(json.load(config)))


def synthesize(text, speaker=None, pitch=1.0, length=1.0):
    if speaker is not None and not 0 <= speaker < voice.config.num_speakers:
        raise ValueError("unknown speaker")
    pitch = min(1.25, max(0.8, float(pitch)))
    length = min(1.5, max(0.6, float(length)))
    chunks = [chunk for chunk in voice.synthesize(text[:500], syn_config=SynthesisConfig(
        speaker_id=speaker, length_scale=voice.config.length_scale * pitch * length))
        if chunk.audio_int16_bytes]
    rate = chunks[0].sample_rate if chunks else voice.config.sample_rate
    # Piper speaks sentence after sentence without a breath; a short pause separates the ideas.
    gap = bytes(2 * round(rate * PAUSE_S))
    frames = gap.join(chunk.audio_int16_bytes for chunk in chunks)
    # Every voice at the same loudness, and pitch by playing the same samples at another rate: pitch and
    # timbre move together, the speed was set above.
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(round(rate * pitch))
        wav.writeframes(normalise(frames))
    return out.getvalue()


PAUSE_S = 0.28  # between two sentences
TARGET_RMS = 10 ** (-17 / 20)  # -17 dBFS while speaking
PEAK = 0.89


def normalise(frames):
    samples = numpy.frombuffer(frames, dtype=numpy.int16).astype(numpy.float32) / 32768
    if not samples.size:
        return frames
    # Loudness of the voiced part only, so pauses do not make a sentence louder.
    level = numpy.abs(samples)
    voiced = samples[level > 0.02]
    rms = float(numpy.sqrt(numpy.mean(voiced ** 2))) if voiced.size else 0.0
    peak = float(level.max())
    if rms < 1e-4 or peak < 1e-4:
        return frames
    gain = min(TARGET_RMS / rms, PEAK / peak)
    return (numpy.clip(samples * gain, -1, 1) * 32767).astype(numpy.int16).tobytes()


if "--server" in sys.argv:
    print('{"ready":true}', flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        try:
            audio = synthesize(request["text"], request.get("speaker"), request.get("pitch", 1.0),
                               request.get("length", 1.0))
            print(json.dumps({"audio": base64.b64encode(audio).decode()}), flush=True)
        except ValueError:
            print('{"error":"invalid request"}', flush=True)
else:
    sys.stdout.buffer.write(synthesize(sys.stdin.read(500)))
