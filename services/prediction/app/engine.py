import asyncio
import os
from dataclasses import dataclass

import chess
import chess.engine

from .models import Probabilities


@dataclass
class EngineEvaluation:
    probabilities: Probabilities
    depth: int | None
    name: str
    evaluation_cp: int | None = None
    mate: int | None = None


def white_score(score):
    white = score.white()
    return white.score(mate_score=10000), white.mate()


def white_wdl(wdl: chess.engine.PovWdl) -> Probabilities:
    white = wdl.white()
    total = white.wins + white.draws + white.losses
    if total <= 0:
        raise ValueError("Engine returned empty WDL")
    return Probabilities(
        white=white.wins / total, draw=white.draws / total, black=white.losses / total
    )


class Stockfish:
    def __init__(self, search_ms: int = 250):
        self.search_ms = search_ms
        self.transport = None
        self.protocol: chess.engine.UciProtocol | None = None
        self.name = "Stockfish"

    async def start(self):
        self.transport, self.protocol = await asyncio.wait_for(
            chess.engine.popen_uci(os.environ.get("STOCKFISH_PATH", "/usr/games/stockfish")),
            timeout=10,
        )
        if "UCI_ShowWDL" not in self.protocol.options:
            await self.close()
            raise RuntimeError("This Stockfish build does not support UCI_ShowWDL")
        await self.protocol.configure(
            {
                "Threads": max(1, min(4, int(os.environ.get("ENGINE_THREADS", "1")))),
                "Hash": max(16, min(256, int(os.environ.get("ENGINE_HASH_MB", "64")))),
                "UCI_ShowWDL": True,
            }
        )
        self.name = self.protocol.id.get("name", "Stockfish")

    async def evaluate(self, board: chess.Board, game_id: str):
        if self.protocol is None:
            await self.start()
        assert self.protocol is not None
        # python-chess sends the entire move stack to UCI, preserving repetition history.
        search = asyncio.create_task(
            self.protocol.analyse(
                board,
                chess.engine.Limit(time=self.search_ms / 1000),
                game=game_id,
                info=chess.engine.INFO_ALL,
            )
        )
        timeout = self.search_ms / 1000 + 2
        try:
            info = await asyncio.wait_for(asyncio.shield(search), timeout=timeout)
        except asyncio.CancelledError:
            # Cancelling python-chess before readyok corrupts its UCI command state.
            # Discard this result, but drain the bounded search before reusing the engine.
            try:
                await asyncio.wait_for(asyncio.shield(search), timeout=timeout)
            except (Exception, asyncio.CancelledError):
                await self.close()
            await asyncio.gather(search, return_exceptions=True)
            raise
        except TimeoutError:
            await self.close()
            await asyncio.gather(search, return_exceptions=True)
            raise
        if "wdl" not in info:
            # A terminal position may have no UCI WDL line. Only use a proven board result.
            outcome = board.outcome(claim_draw=False)
            if outcome is None:
                raise RuntimeError("Stockfish did not return WDL")
            probabilities = Probabilities(
                white=float(outcome.winner is chess.WHITE),
                draw=float(outcome.winner is None),
                black=float(outcome.winner is chess.BLACK),
            )
        else:
            probabilities = white_wdl(info["wdl"])
        cp, mate = white_score(info["score"]) if "score" in info else (None, None)
        return EngineEvaluation(probabilities, info.get("depth"), self.name, cp, mate)

    async def close(self):
        protocol, transport = self.protocol, self.transport
        self.protocol = self.transport = None
        if protocol is not None:
            try:
                await asyncio.wait_for(protocol.quit(), timeout=1)
            except (Exception, asyncio.CancelledError):
                pass
        if transport is not None:
            transport.close()
