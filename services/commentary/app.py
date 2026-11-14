"""Internal HTTP worker. No browser access, persistent audio or credentials in logs."""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass

import chess.engine
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from core import Job, PHRASEBOOK, comment_id, cp_loss, engine_score, extract, speech_text
from narrative import brief, compose
from phrases import (INTRO, OPENING_CENTRAL, OPENING_FLEXIBLE, OPENING_GENERAL, OPENING_RARE, TRANSITIONS, dress,
                     seed_of, shape)
from voices import CATALOGUE, DEFAULT, SAMPLE, VOICES, Voice

# Spoken from audio made in advance, with no wait at all: the voice sample, bridges, greetings, first moves.
PREPARED = (SAMPLE, *TRANSITIONS, *INTRO, *OPENING_CENTRAL, *OPENING_FLEXIBLE, *OPENING_GENERAL, *OPENING_RARE)

# A move accepted sooner than this after the previous one: the players are moving fast.
FAST_MS = 3500
# A second subscription waits this long for the single worker before getting 429.
BUSY_WAIT_S = 2.5
# A sentence about the move this long gets only the shortest reading: four ideas in one breath were too many.
LONG_BASE = 60

log = logging.getLogger("commentary")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(logging.StreamHandler())


@dataclass
class Settings:
    token: str = os.getenv("BLV_TOKEN", "")
    stockfish: str = os.getenv("BLV_STOCKFISH", "stockfish")
    eval_file: str = os.getenv("BLV_EVAL_FILE", "")
    engine_ms: int = int(os.getenv("BLV_ENGINE_MS", "100"))
    ttl_ms: int = int(os.getenv("BLV_TTL_MS", "12000"))
    tts_timeout: float = float(os.getenv("BLV_TTS_TIMEOUT", "4"))
    # The default voice's model; the other voices' models are looked up next to it, or in BLV_PIPER_DIR.
    piper_model: str = os.getenv("BLV_PIPER_MODEL", "")
    piper_dir: str = os.getenv("BLV_PIPER_DIR", "")
    # Engine scores and PVs stay inside the service unless explicitly debugging:
    # the payload goes to a player's browser during a live game.
    debug_facts: bool = os.getenv("BLV_DEBUG_FACTS", "") == "1"


class Recent:
    """What was said lately in each game, so the next comment picks a fresh sentence and reading."""

    def __init__(self, games=64, window=16):
        self.games = OrderedDict()
        self.moves = OrderedDict()
        self.size, self.window = games, window

    def said(self, game_id):
        return [(text, motif) for text, motif, *_ in self.games.get(game_id, ())]

    def spoken(self, game_id):
        """The full lines as read aloud, lead-ins included."""
        return [entry[2] for entry in self.games.get(game_id, ())]

    def motifs(self, game_id):
        return [motif for _, motif in self.said(game_id) if motif]

    def insights(self, game_id):
        """Readings added lately, as "key:subject"."""
        return [entry[3] for entry in self.games.get(game_id, ()) if len(entry) > 3 and entry[3]]

    def order(self, game_id, alternatives):
        # "Liệu Trắng…" after "Liệu Đen…", or "mã tại f3" after "tượng tại c4", is the same sentence again.
        last = {}
        for index, entry in enumerate(self.games.get(game_id, ())):
            last[shape(entry[0])] = index
            if len(entry) > 4 and entry[4]:
                last[shape(entry[4])] = index
        # Unsaid sentences first (in their composed order), then the longest-ago one.
        return sorted(alternatives, key=lambda text: last.get(shape(text), -1))

    def fresh(self, game_id, alternatives):
        """The ordered alternatives as fresh as the first one."""
        ordered = self.order(game_id, alternatives)
        rank = {shape(entry[0]): index for index, entry in enumerate(self.games.get(game_id, ()))}
        first = rank.get(shape(ordered[0]), -1)
        return [text for text in ordered if rank.get(shape(text), -1) == first]

    def pace(self, game_id, ply, accepted_at):
        """Milliseconds since the previous move of the game was accepted, or None."""
        previous = self.moves.pop(game_id, None)
        self.moves[game_id] = (ply, accepted_at)
        while len(self.moves) > self.size:
            self.moves.popitem(last=False)
        if previous and ply == previous[0] + 1:
            return accepted_at - previous[1]
        return None

    def record(self, game_id, text, motif=None, spoken=None, insight=None, insight_text=None):
        history = self.games.pop(game_id, None) or deque(maxlen=self.window)
        history.append((text, motif, spoken or text, insight, insight_text))
        self.games[game_id] = history
        while len(self.games) > self.size:
            self.games.popitem(last=False)


class Piper:
    """One Piper process for one model file; the voices of that model share it, one sentence at a time."""

    def __init__(self, path: Path):
        self.path = path
        self.process = None
        self.lock = asyncio.Lock()

    @property
    def alive(self):
        return self.process is not None and self.process.returncode is None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).with_name("piper_worker.py")), str(self.path), "--server",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=2_000_000)
        try:
            ready = await asyncio.wait_for(self.process.stdout.readline(), 10)
            if not ready or not json.loads(ready).get("ready"):
                raise ValueError("Piper unavailable")
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        if self.process:
            if self.process.returncode is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
            await self.process.wait()
            self.process = None

    async def ensure(self):
        async with self.lock:
            if not self.alive:
                await self.start()

    async def say(self, text, voice: Voice, timeout) -> bytes:
        async with self.lock:
            if not self.alive:
                await self.start()
            try:
                request = {"text": text, "speaker": voice.speaker, "pitch": voice.pitch, "length": voice.length}
                self.process.stdin.write((json.dumps(request) + "\n").encode())
                await self.process.stdin.drain()
                response = await asyncio.wait_for(self.process.stdout.readline(), timeout)
                audio = base64.b64decode(json.loads(response)["audio"], validate=True)
                if not audio.startswith(b"RIFF") or len(audio) > 1_500_000:
                    raise ValueError("Piper synthesis failed")
                return audio
            except BaseException:
                await self.stop()
                raise


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = None
        self.transport = None
        self.busy = False
        # Installed voices, catalogue order (the default first).
        self.voices: list[str] = []
        self.engine_name = None
        self.active_id = None
        self.active_task = None
        self.cancelled = OrderedDict()
        # Model path -> its Piper process, started on first use.
        self.pipers: dict[str, Piper] = {}
        self.audio_cache = OrderedDict()
        # Voice -> text -> audio of PREPARED, filled in the background the first time a voice is used.
        self.prepared: dict[str, dict[str, dict]] = {}
        self.warming: dict[str, asyncio.Task] = {}
        self.preparing = None
        self.recent = Recent()
        self.active_event = None

    def model_path(self, voice: Voice) -> Path | None:
        default = Path(self.settings.piper_model) if self.settings.piper_model else None
        # BLV_PIPER_MODEL is the default voice's model, whatever its file name; voices of that model share it.
        if default and voice.model == VOICES[DEFAULT].model:
            return default
        folder = self.settings.piper_dir or (default and default.parent)
        return Path(folder) / f"{voice.model}.onnx" if folder else None

    def find_voices(self):
        self.voices = [voice.id for voice in CATALOGUE
                       if (path := self.model_path(voice)) and path.is_file() and Path(f"{path}.json").is_file()]

    def voice_for(self, wanted):
        """The voice to speak with: the one asked for if its model is installed, otherwise the default."""
        return wanted if wanted in self.voices else DEFAULT

    def piper_for(self, voice) -> Piper:
        path = self.model_path(VOICES[voice])
        return self.pipers.setdefault(str(path), Piper(path))

    def ensure_warm(self, voice) -> asyncio.Task:
        if voice not in self.warming:
            self.warming[voice] = asyncio.create_task(self.warm_speech(voice))
        return self.warming[voice]

    async def warm_all(self):
        """Every installed voice, one after another, so a first sample or greeting has no wait either."""
        for voice in self.voices:
            await self.ensure_warm(voice)

    async def warm_speech(self, voice):
        prepared = self.prepared.setdefault(voice, {})
        try:
            for text in PREPARED:
                if text not in prepared:
                    prepared[text] = await self.voice_audio(speech_text(text), voice)
        except Exception:
            # Live sentences still work; the next use of this voice tries again.
            log.warning("could not prepare voice %s", voice)
            self.warming.pop(voice, None)

    def transition(self, job, voice):
        """A bridge line the browser may say first when it skipped moves."""
        text = TRANSITIONS[seed_of(job.requestId) % len(TRANSITIONS)]
        audio = self.prepared.get(voice, {}).get(text)
        return {"text": text, "audio": audio} if audio else None

    async def start_engine(self):
        self.transport, self.engine = await asyncio.wait_for(
            chess.engine.popen_uci(self.settings.stockfish), 8)
        ping = None
        try:
            options = {"Threads": 1, "Hash": 16}
            if self.settings.eval_file:
                options["EvalFile"] = self.settings.eval_file
            await self.engine.configure(options)
            # A cold NNUE load can take longer than a search. Shield the ping
            # future so a late readyok cannot complete an already-cancelled future.
            ping = asyncio.create_task(self.engine.ping())
            await asyncio.wait_for(asyncio.shield(ping), 8)
            self.engine_name = self.engine.id.get("name")
        except BaseException:
            self.transport.close()
            self.engine = None
            if ping:
                await asyncio.gather(ping, return_exceptions=True)
            raise

    async def close(self):
        if self.transport:
            self.transport.close()
        self.engine = None
        tasks = [*self.warming.values(), *([self.preparing] if self.preparing else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for piper in self.pipers.values():
            await piper.stop()

    async def search(self, board, limit, mover):
        # AnalysisResult.info merges successive UCI packets. A lower/upperbound
        # from an earlier aspiration search can otherwise stick to a later exact
        # score. Keep the last completed exact iteration, with depth/PV from
        # that SAME packet. An interrupted next iteration may end on a bound.
        latest = None
        completed = None
        with await self.engine.analysis(board, limit) as analysis:
            async for info in analysis:
                if "score" in info:
                    latest = info
                    if not info.get("lowerbound") and not info.get("upperbound"):
                        completed = info
            await analysis.wait()
        if latest is None:
            raise chess.engine.EngineError("search returned no score")
        return engine_score(completed or latest, mover)

    async def analyse(self, facts):
        if self.engine is None:
            await self.start_engine()
        mover = facts.before.turn
        try:
            limit = chess.engine.Limit(time=self.settings.engine_ms / 1000)
            before = await asyncio.wait_for(self.search(facts.before, limit, mover), 2)
            after = await asyncio.wait_for(self.search(facts.board, limit, mover), 2)
            loss = cp_loss(before, after)
            sensitive = ((loss is not None and loss >= 100)
                         or facts.values.get("position", {}).get("checkForkTargets")
                         or before.get("mate") is not None or after.get("mate") is not None)
            confirmed = False
            if sensitive:
                deeper = chess.engine.Limit(time=max(.2, self.settings.engine_ms / 500))
                refined_before = await asyncio.wait_for(self.search(facts.before, deeper, mover), 2)
                refined_after = await asyncio.wait_for(self.search(facts.board, deeper, mover), 2)
                refined_loss = cp_loss(refined_before, refined_after)
                confirmed = bool((loss is not None and refined_loss is not None and abs(loss - refined_loss) <= 80)
                                 or (after.get("mate") and refined_after.get("mate")
                                     and (after["mate"] > 0) == (refined_after["mate"] > 0))
                                 or (before.get("mate") and refined_before.get("mate")
                                     and before["mate"] > 0 and refined_before["mate"] > 0
                                     and after.get("cp") is not None and refined_after.get("cp") is not None
                                     and abs(after["cp"] - refined_after["cp"]) <= 80))
                before, after, loss = refined_before, refined_after, refined_loss
            return {"source": self.engine_name, "threads": 1, "hashMiB": 16,
                    "budgetMsPerPosition": self.settings.engine_ms, "before": before, "after": after,
                    "lossCpHeuristic": loss, "confirmed": confirmed, "rechecked": bool(sensitive)}
        except (asyncio.CancelledError, asyncio.TimeoutError, TimeoutError, chess.engine.EngineError, OSError):
            if self.transport:
                self.transport.close()
            self.engine = None
            raise

    async def voice_audio(self, text, voice):
        if voice not in self.voices:
            raise ValueError("voice not installed")
        audio = await self.piper_for(voice).say(text, VOICES[voice], self.settings.tts_timeout)
        return {"mime": "audio/wav", "base64": base64.b64encode(audio).decode(),
                "provider": "piper-tts:1.8.0", "voice": voice}

    async def synthesize(self, text, voice):
        key = (voice, text)
        if key in self.audio_cache:
            self.audio_cache.move_to_end(key)
            return self.audio_cache[key]
        # A request cancelled mid-sentence (a move during an idle remark) must not
        # kill Piper: reloading it would delay the next comment. The sentence
        # finishes in the background; Piper's lock keeps requests from interleaving.
        return await asyncio.shield(self.cached_audio(key, text, voice))

    async def cached_audio(self, key, text, voice):
        result = await self.voice_audio(text, voice)
        # Only reusable speech bytes, never request IDs or player data.
        self.audio_cache[key] = result
        if len(self.audio_cache) > 64:
            self.audio_cache.popitem(last=False)
        return result

    async def sample(self, wanted):
        """The sample sentence in a voice (or the default when that one is not installed), as WAV bytes."""
        voice = self.voice_for(wanted)
        audio = self.prepared.get(voice, {}).get(SAMPLE)
        if audio is None:
            self.ensure_warm(voice)
            audio = await self.synthesize(speech_text(SAMPLE), voice)
        return voice, base64.b64decode(audio["base64"])

    def envelope(self, job, facts):
        result = {"schemaVersion": 1, "requestId": job.requestId, "gameId": job.gameId,
                  "gameRevision": job.gameRevision, "ply": job.ply, "fenAfter": job.fenAfter,
                  "status": job.status,
                  "expiresAt": job.expiresAt, "acceptedAt": job.acceptedAt,
                  "commentId": comment_id(job, facts), "priority": facts.priority,
                  "kind": self.kind(facts), "factIds": [self.kind(facts)],
                  "phrasebook": PHRASEBOOK}
        if self.settings.debug_facts:
            result["facts"] = facts.values
        return result

    def theme(self, facts):
        return facts.values.get("narration", {}).get("theme")

    @staticmethod
    def kind(facts):
        return next((kind for kind in ("finish", "intro", "idle") if kind in facts.values), "move")

    def cached_start(self, job):
        if job.event == "idle" or (job.event != "intro" and job.ply != 1):
            return None
        facts = extract(job)
        if "intro" not in facts.values:
            compose(facts, job)
            if not (self.theme(facts) or "").startswith("opening_"):
                return None
        text = facts.alternatives[0]
        voice = self.voice_for(job.voice)
        audio = self.prepared.get(voice, {}).get(text)
        if not audio:
            return None
        # Greetings and verified opening phrases need no live engine or Piper access. They must not wait for
        # a cancelled previous game's voice to reload, or steal its worker slot.
        result = self.envelope(job, facts)
        result["factIds"] = facts.values.get("narration", {}).get("factIds", result["factIds"])
        result.update(type="comment", text=text, speechText=speech_text(text), audio=audio,
                      timings={"engineMs": 0, "composedMs": 0, "audioReadyMs": 0})
        self.recent.record(job.gameId, text, self.theme(facts))
        if bridge := self.transition(job, voice):
            result["transition"] = bridge
        return result

    async def process(self, job: Job, disconnected):
        started = time.monotonic()
        timings = {}
        def checkpoint():
            if time.time() * 1000 >= job.expiresAt:
                raise HTTPException(410, "expired job")
        async def check():
            checkpoint()
            if await disconnected():
                raise HTTPException(410, "disconnected")
        await check()
        facts = extract(job)
        result = self.envelope(job, facts)
        if not facts.selected:
            return result | {"type": "silent", "reason": "quiet_position"}
        voice = self.voice_for(job.voice)
        if voice in self.voices:
            self.ensure_warm(voice)
            # Recreate a cancelled/crashed voice before the engine works, not after the sentence is ready.
            if not self.piper_for(voice).alive:
                try:
                    await self.piper_for(voice).ensure()
                except Exception:
                    pass
        await check()
        if self.kind(facts) == "move":
            try:
                facts.values["engine"] = await self.analyse(facts)
            except (asyncio.TimeoutError, TimeoutError, chess.engine.EngineError, OSError):
                facts.values["engine"] = {"unavailable": True}
        timings["engineMs"] = round((time.monotonic() - started) * 1000)
        await check()
        # Players moving quickly: the shortest fresh sentence, only striking readings, no lead-ins.
        fast = False
        if result["kind"] == "move":
            gap = self.recent.pace(job.gameId, job.ply, job.acceptedAt)
            fast = gap is not None and gap < FAST_MS
        compose(facts, job, self.recent.motifs(job.gameId), self.recent.insights(job.gameId), fast)
        # Sentences not said lately in this game first, then the one said longest ago.
        facts.alternatives = self.recent.order(job.gameId, facts.alternatives)
        result.update(commentId=comment_id(job, facts), priority=facts.priority,
                      factIds=facts.values.get("narration", {}).get("factIds", result["factIds"]))
        base = text = facts.alternatives[0]
        if fast:
            # "Xe Trắng ăn mã d7." is over before the next move; a fork with check keeps its sentence.
            base = text = (min(self.recent.fresh(job.gameId, facts.alternatives), key=len)
                           if "check_fork" in (self.theme(facts) or "") else brief(facts))
        reading = ident = None
        if facts.insight:
            readings = self.recent.order(job.gameId, facts.insight.texts)
            # After a long sentence (a capture with check and a threat) the reading stays short.
            reading = min(readings, key=len) if fast or len(base) > LONG_BASE else readings[0]
            ident = f"{facts.insight.key}:{facts.insight.subject}"
        # Greetings, first moves and endings are complete as written; a reading replaces the lead-in.
        if (result["kind"] in ("move", "idle") and not (self.theme(facts) or "").startswith("opening_")
                and not reading and not fast):
            to_move = "Trắng" if facts.board.turn else "Đen"
            seed = seed_of(job.gameId, " ".join(job.sanHistory), job.idleCount, base)
            text = dress(self.theme(facts), base, to_move, seed, self.recent.spoken(job.gameId))
        if reading:
            text = f"{base} {reading}"
        timings["composedMs"] = round((time.monotonic() - started) * 1000)
        await check()
        speech = speech_text(text)
        result.update(text=text, speechText=speech)
        if bridge := self.transition(job, voice):
            result["transition"] = bridge
        try:
            result["audio"] = await self.synthesize(speech, voice)
        except Exception:
            result["audio"] = None
            result["audioError"] = "tts_unavailable"
        await check()
        timings["audioReadyMs"] = round((time.monotonic() - started) * 1000)
        result.update(type="comment", timings=timings)
        # The sentence, and the whole line with its lead-in and closing line, so neither repeats soon.
        self.recent.record(job.gameId, base, self.theme(facts), text, ident, reading)
        # Request identifiers and timing only; no player names, cookies, keys, text or audio.
        log.info(json.dumps({"requestId": job.requestId, "kind": result["kind"], "theme": self.theme(facts),
                             "dressed": text != base, "insight": ident, "fast": fast, "voice": voice, "audio": bool(result["audio"]), **timings}))
        return result


def create_app(settings: Settings | None = None, worker: Worker | None = None):
    settings = settings or Settings()
    worker = worker or Worker(settings)

    @asynccontextmanager
    async def lifespan(app):
        if not settings.token:
            raise RuntimeError("BLV_TOKEN is required")
        try:
            await worker.start_engine()
        except (OSError, asyncio.TimeoutError, TimeoutError, chess.engine.EngineError):
            log.warning("engine unavailable; factual baseline remains available")
        worker.find_voices()
        if DEFAULT in worker.voices:
            # The default voice is ready before the first request; the others prepare in the background.
            try:
                await worker.piper_for(DEFAULT).ensure()
                await worker.ensure_warm(DEFAULT)
            except Exception:
                log.warning("offline TTS unavailable")
        worker.preparing = asyncio.create_task(worker.warm_all())
        try:
            yield
        finally:
            await worker.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.worker = worker

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        details = [{"loc": value["loc"], "type": value["type"]} for value in error.errors()]
        log.warning("Invalid commentary contract: %s", details)
        return JSONResponse(status_code=422, content={"detail": details})

    @app.middleware("http")
    async def failure_status(request, call_next):
        response = await call_next(request)
        if response.status_code >= 400:
            log.warning("Commentary HTTP %s on %s", response.status_code, request.url.path)
        return response

    def authorize(authorization: str = Header(default="")):
        if not settings.token or not secrets.compare_digest(authorization, "Bearer " + settings.token):
            raise HTTPException(401, "unauthorized")

    @app.get("/health", dependencies=[Depends(authorize)])
    async def health():
        speaking = DEFAULT in worker.voices and worker.piper_for(DEFAULT).alive
        return {"alive": True, "ready": bool(worker.engine and speaking),
                "engine": worker.engine_name, "voices": worker.voices,
                "prepared": [voice for voice in worker.voices
                             if len(worker.prepared.get(voice, ())) == len(PREPARED)],
                "phrasebook": PHRASEBOOK, "busy": worker.busy}

    @app.get("/sample/{voice}", dependencies=[Depends(authorize)])
    async def sample(voice: str):
        if voice not in VOICES:
            raise HTTPException(404, "unknown voice")
        try:
            used, audio = await asyncio.wait_for(worker.sample(voice), 8)
        except Exception as exc:
            raise HTTPException(503, "voice unavailable") from exc
        return Response(audio, media_type="audio/wav", headers={"X-Voice": used})

    @app.post("/comment", dependencies=[Depends(authorize)])
    async def comment(job: Job, request: Request):
        now = time.time() * 1000
        if job.expiresAt <= now or job.expiresAt > now + settings.ttl_ms + 1000:
            raise HTTPException(410, "invalid expiry")
        if job.requestId in worker.cancelled:
            raise HTTPException(410, "cancelled job")
        if job.event == "intro" or job.ply == 1:
            try:
                cached = worker.cached_start(job)
                if cached is not None:
                    return cached
            except ValueError as exc:
                raise HTTPException(422, "invalid chess snapshot") from exc
        # A move must not wait behind a remark about the position before it.
        if worker.busy and worker.active_event == "idle" and job.event != "idle" and worker.active_task:
            worker.active_task.cancel()
            for _ in range(20):
                if not worker.busy:
                    break
                await asyncio.sleep(0.05)
        # One worker, no unbounded HTTP/UCI queue. Lila holds only the newest pending state per
        # subscription, but two subscriptions (both players, two tabs) send at the same instant:
        # a job takes ~0.5 s, so the second one waits briefly instead of losing its move.
        waited = 0.0
        while worker.busy and waited < BUSY_WAIT_S:
            await asyncio.sleep(0.05)
            waited += 0.05
            if job.requestId in worker.cancelled:
                raise HTTPException(410, "cancelled job")
        if worker.busy:
            raise HTTPException(429, "worker busy")
        worker.busy = True
        worker.active_id = job.requestId
        worker.active_event = job.event
        worker.active_task = asyncio.create_task(worker.process(job, request.is_disconnected))
        try:
            return await asyncio.wait_for(worker.active_task, settings.ttl_ms / 1000)
        except asyncio.CancelledError as exc:
            raise HTTPException(410, "cancelled job") from exc
        except ValueError as exc:
            raise HTTPException(422, "invalid chess snapshot") from exc
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise HTTPException(504, "job timeout") from exc
        finally:
            worker.busy = False
            worker.active_id = None
            worker.active_event = None
            worker.active_task = None

    @app.post("/cancel/{request_id}", dependencies=[Depends(authorize)])
    async def cancel(request_id: str):
        if len(request_id) > 120:
            raise HTTPException(422, "invalid request id")
        now = time.monotonic()
        while worker.cancelled and (next(iter(worker.cancelled.values())) < now or len(worker.cancelled) >= 256):
            worker.cancelled.popitem(last=False)
        worker.cancelled[request_id] = now + settings.ttl_ms / 1000
        if worker.active_id == request_id and worker.active_task:
            worker.active_task.cancel()
        return {"cancelled": True}

    return app


app = create_app()
