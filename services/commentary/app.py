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
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass

import chess.engine
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core import Job, PROMPT_VERSION, comment_id, cp_loss, engine_score, extract, validate_realization
from narrative import INTRO_PHRASES, OPENING_CENTRAL, OPENING_FLEXIBLE, OPENING_GENERAL, TRANSITION_TEXT, compose

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
    llm_url: str = os.getenv("BLV_LLM_URL", "")
    llm_key: str = os.getenv("BLV_LLM_KEY", "")
    llm_model: str = os.getenv("BLV_LLM_MODEL", "qwen2.5-1.5b-instruct")
    llm_timeout: float = float(os.getenv("BLV_LLM_TIMEOUT", "2"))
    tts_timeout: float = float(os.getenv("BLV_TTS_TIMEOUT", "4"))
    piper_model: str = os.getenv("BLV_PIPER_MODEL", "")


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = None
        self.transport = None
        self.busy = False
        self.http = httpx.AsyncClient(trust_env=False)
        self.voices: list[str] = []
        self.engine_name = None
        self.active_id = None
        self.active_task = None
        self.cancelled = OrderedDict()
        self.piper = None
        self.audio_cache = OrderedDict()
        self.transition_audio = None
        self.intro_audio = {}
        self.opening_audio = {}

    async def warm_speech(self):
        self.transition_audio = await self.synthesize(TRANSITION_TEXT, "piper-vais1000")
        for text in INTRO_PHRASES:
            self.intro_audio[text] = await self.synthesize(text, "piper-vais1000")
        for text in OPENING_CENTRAL + OPENING_FLEXIBLE + OPENING_GENERAL:
            self.opening_audio[text] = await self.synthesize(text, "piper-vais1000")

    async def start_piper(self):
        self.piper = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).with_name("piper_worker.py")), self.settings.piper_model, "--server",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=2_000_000)
        try:
            ready = await asyncio.wait_for(self.piper.stdout.readline(), 10)
            if not ready or not json.loads(ready).get("ready"):
                raise ValueError("Piper unavailable")
        except BaseException:
            await self.stop_piper()
            raise

    async def stop_piper(self):
        if self.piper:
            if self.piper.returncode is None:
                try:
                    self.piper.kill()
                except ProcessLookupError:
                    pass
            await self.piper.wait()
            self.piper = None

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
        await self.stop_piper()
        await self.http.aclose()

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

    async def realize(self, facts, timeout=None):
        if not self.settings.llm_url or len(facts.alternatives) == 1:
            return facts.alternatives[0], "baseline", None
        if timeout is not None and timeout <= 0:
            return facts.alternatives[0], "baseline", "llm_budget"
        ids = facts.values.get("narration", {}).get("factIds", ["intro"] if "intro" in facts.values else ["finish"] if "finish" in facts.values else ["move"])
        move = {key: value for key, value in facts.values.get("move", {}).items()
                if key not in ("source", "san") and value not in (None, False)}
        payload = {"model": self.settings.llm_model, "temperature": 0, "max_tokens": 320,
                   "messages": [
                       {"role": "system", "content": "Narrate both sides of this chess position in lively Vietnamese. Copy exactly one complete verified commentary from alternatives and its required factIds. Return only the schema JSON. Never invent tactical claims or future moves."},
                       {"role": "user", "content": json.dumps({"facts": {"move": move}, "factIds": ids, "alternatives": facts.alternatives}, ensure_ascii=False, separators=(",", ":"))}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "commentary", "strict": True, "schema": {
                           "type": "object", "additionalProperties": False,
                           "properties": {"text": {"type": "string", "enum": facts.alternatives},
                                          "factIds": {"type": "array", "items": {"type": "string"},
                                                      "const": ids}},
                           "required": ["text", "factIds"]}}}}
        headers = {"Authorization": f"Bearer {self.settings.llm_key}"} if self.settings.llm_key else {}
        try:
            response = await self.http.post(self.settings.llm_url.rstrip("/") + "/chat/completions",
                                            json=payload, headers=headers,
                                            timeout=self.settings.llm_timeout if timeout is None else timeout)
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            return validate_realization(raw, facts), "llm", None
        except httpx.TimeoutException:
            return facts.alternatives[0], "baseline", "llm_timeout"
        except httpx.HTTPError:
            return facts.alternatives[0], "baseline", "llm_unavailable"
        except (ValueError, KeyError, IndexError, TypeError):
            return facts.alternatives[0], "baseline", "llm_unverified"

    async def synthesize(self, text, voice):
        key = (voice, text)
        if key in self.audio_cache:
            self.audio_cache.move_to_end(key)
            return self.audio_cache[key]
        if voice == "piper-vais1000":
            if not self.settings.piper_model:
                raise ValueError("Piper model missing")
            if not self.piper or self.piper.returncode is not None:
                await self.start_piper()
            try:
                self.piper.stdin.write((json.dumps({"text": text}) + "\n").encode())
                await self.piper.stdin.drain()
                response = await asyncio.wait_for(self.piper.stdout.readline(), self.settings.tts_timeout)
                audio = base64.b64decode(json.loads(response)["audio"], validate=True)
                if not audio.startswith(b"RIFF") or len(audio) > 1_500_000:
                    raise ValueError("Piper synthesis failed")
                result = {"mime": "audio/wav", "base64": base64.b64encode(audio).decode(),
                          "provider": "piper-tts:1.8.0", "voice": voice}
                # Only reusable speech bytes, never request IDs or player data.
                self.audio_cache[key] = result
                if len(self.audio_cache) > 32:
                    self.audio_cache.popitem(last=False)
                return result
            except BaseException:
                await self.stop_piper()
                raise
        raise ValueError("unsupported voice")

    @staticmethod
    def envelope(job, facts):
        return {"schemaVersion": 1, "requestId": job.requestId, "gameId": job.gameId,
                  "gameRevision": job.gameRevision, "ply": job.ply, "fenAfter": job.fenAfter,
                  "status": job.status,
                  "expiresAt": job.expiresAt, "acceptedAt": job.acceptedAt,
                  "commentId": comment_id(job, facts), "priority": facts.priority,
                  "kind": "intro" if "intro" in facts.values else "finish" if "finish" in facts.values else "move",
                  "factIds": ["finish"] if "finish" in facts.values else ["intro"] if "intro" in facts.values else ["move"],
                  "facts": facts.values, "promptVersion": PROMPT_VERSION}

    def cached_start(self, job):
        if job.event != "intro" and job.ply != 1:
            return None
        facts = extract(job)
        if "intro" in facts.values:
            audio_map = self.intro_audio
        else:
            compose(facts, job)
            if not facts.values.get("narration", {}).get("theme", "").startswith("opening_"):
                return None
            audio_map = self.opening_audio
        text = facts.alternatives[0]
        audio = audio_map.get(text)
        if not audio:
            return None
        # Greetings and verified opening phrases need no live engine or Piper access. They must not wait for
        # a cancelled previous game's voice to reload, or steal its worker slot.
        result = self.envelope(job, facts)
        result["factIds"] = facts.values.get("narration", {}).get("factIds", result["factIds"])
        result.update(type="comment", text=text, speechText=text, audio=audio,
                      generator="baseline", fallback=None,
                      timings={"engineMs": 0, "validatedMs": 0, "audioReadyMs": 0, "llmBudgetMs": 0})
        if self.transition_audio:
            result["transition"] = {"text": TRANSITION_TEXT, "audio": self.transition_audio}
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
        # Recreate a cancelled/crashed voice before inference, while there is
        # still enough deadline budget to load it, rather than after a slow LLM.
        if job.voice == "piper-vais1000" and self.settings.piper_model and (
                self.piper is None or self.piper.returncode is not None):
            try:
                await self.start_piper()
            except Exception:
                pass
        await check()
        if "intro" not in facts.values and "finish" not in facts.values:
            try:
                facts.values["engine"] = await self.analyse(facts)
            except (asyncio.TimeoutError, TimeoutError, chess.engine.EngineError, OSError):
                facts.values["engine"] = {"unavailable": True}
        timings["engineMs"] = round((time.monotonic() - started) * 1000)
        await check()
        compose(facts, job)
        result.update(commentId=comment_id(job, facts), priority=facts.priority,
                      factIds=facts.values.get("narration", {}).get("factIds", result["factIds"]))
        remaining = (job.expiresAt - time.time() * 1000) / 1000
        llm_budget = max(0, min(self.settings.llm_timeout, remaining - self.settings.tts_timeout - 0.5))
        timings["llmBudgetMs"] = round(llm_budget * 1000)
        text, generator, fallback = await self.realize(facts, timeout=llm_budget)
        timings["validatedMs"] = round((time.monotonic() - started) * 1000)
        await check()
        result.update(text=text, speechText=text, generator=generator, fallback=fallback)
        if self.transition_audio:
            result["transition"] = {"text": TRANSITION_TEXT, "audio": self.transition_audio}
        try:
            result["audio"] = await self.synthesize(text, job.voice)
        except Exception:
            result["audio"] = None
            result["audioError"] = "tts_unavailable"
        await check()
        timings["audioReadyMs"] = round((time.monotonic() - started) * 1000)
        result.update(type="comment", timings=timings)
        # Request identifiers and timing only; no player names, cookies, keys, text or audio.
        log.info(json.dumps({"requestId": job.requestId, "generator": generator, "fallback": fallback,
                             "audio": bool(result["audio"]), **timings}))
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
        if Path(settings.piper_model).is_file():
            try:
                await worker.start_piper()
                worker.voices.insert(0, "piper-vais1000")
                await worker.warm_speech()
            except Exception:
                log.warning("offline TTS unavailable")
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
        ready = bool(worker.engine and worker.piper and worker.piper.returncode is None)
        return {"alive": True, "ready": ready,
                "engine": worker.engine_name, "voices": worker.voices,
                "generator": "llm-with-validated-baseline" if settings.llm_url else "baseline",
                "model": settings.llm_model if settings.llm_url else None, "busy": worker.busy}

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
        # One worker, no unbounded HTTP/UCI queue. Lila holds only the newest pending state.
        if worker.busy:
            raise HTTPException(429, "worker busy")
        worker.busy = True
        worker.active_id = job.requestId
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
