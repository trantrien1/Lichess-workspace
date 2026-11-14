import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from .engine import Stockfish
from .human import load_model
from .models import HumanPrediction, PositionRequest, Prediction
from .scheduler import Scheduler

logger = logging.getLogger(__name__)


def make_app(scheduler: Scheduler | None = None, human_model=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        search_ms = max(50, min(2000, int(os.environ.get("ENGINE_TIME_MS", "250"))))
        app.state.scheduler = scheduler or Scheduler(
            workers=max(1, min(4, int(os.environ.get("ENGINE_WORKERS", "1")))),
            engine_factory=lambda: Stockfish(search_ms),
        )
        app.state.scheduler.start()
        app.state.human_model = human_model
        app.state.human_errors = 0
        if human_model is None and os.environ.get("HUMAN_MODEL_ENABLED", "true").lower() == "true":
            path = os.environ.get(
                "HUMAN_MODEL_PATH",
                str(Path(__file__).resolve().parents[1] / "models/human-v1.json"),
            )
            try:
                app.state.human_model = load_model(path)
            except (OSError, ValueError) as exc:
                logger.warning("Human model unavailable; engine service continues: %s", exc)
        yield
        await app.state.scheduler.close()

    app = FastAPI(title="Lichess WDL worker", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health(request: Request):
        model = request.app.state.human_model
        return {
            "status": "ok",
            **request.app.state.scheduler.stats(),
            "human_model": {
                "loaded": model is not None,
                "model_id": model.artifact.model_id if model else None,
                "errors": request.app.state.human_errors,
            },
        }

    @app.post("/v1/predictions", response_model=Prediction, response_model_exclude_none=True)
    async def predict(position: PositionRequest, request: Request):
        try:
            prediction = request.app.state.scheduler.submit(position)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if prediction.status != "ready":
            return prediction
        human = HumanPrediction(status="unavailable", reason="model_unavailable")
        if model := request.app.state.human_model:
            try:
                human = model.predict(
                    position.board(), position.context, prediction.evaluation_cp, prediction.mate
                )
            except Exception:
                request.app.state.human_errors += 1
                logger.exception("Human inference failed; returning the engine result")
        # Never mutate the shared engine cache with a particular spectator request's context.
        return prediction.model_copy(update={"human": human})

    return app


app = make_app()
