"""Killable, one-thread offline TTS subprocess; stdout contains WAV only."""

import io
import base64
import json
import sys
import wave

import onnxruntime
from piper import PiperVoice
from piper.config import PiperConfig

model = sys.argv[1]
options = onnxruntime.SessionOptions()
options.intra_op_num_threads = 1
options.inter_op_num_threads = 1
session = onnxruntime.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])
with open(model + ".json", encoding="utf-8") as config:
    voice = PiperVoice(session, PiperConfig.from_dict(json.load(config)))
def synthesize(text):
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        voice.synthesize_wav(text[:500], wav)
    return audio.getvalue()


if "--server" in sys.argv:
    print('{"ready":true}', flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        print(json.dumps({"audio": base64.b64encode(synthesize(request["text"])).decode()}), flush=True)
else:
    sys.stdout.buffer.write(synthesize(sys.stdin.read(500)))
