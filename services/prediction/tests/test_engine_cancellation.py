import asyncio

import chess
import chess.engine
import pytest

from app.engine import Stockfish


class DelayedReadyTransport(chess.engine.MockTransport):
    """A UCI peer that receives stop while its isready reply is still in transit."""

    def write(self, data):
        if data == b"stop\n":
            return
        super().write(data)


async def test_cancellation_during_uci_handshake_keeps_next_game_usable():
    protocol = chess.engine.UciProtocol()
    transport = DelayedReadyTransport(protocol)
    transport.expect("uci", ["id name TestUCI", "uciok"])
    await protocol.initialize()
    engine = Stockfish(search_ms=50)
    engine.protocol = protocol
    transport.expect("ucinewgame")
    transport.expect("isready")
    first = asyncio.create_task(engine.evaluate(chess.Board(), "game0001"))
    async with asyncio.timeout(2):
        while transport.expectations:
            await asyncio.sleep(0)
    first.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    transport.expect("position startpos")
    transport.expect("go movetime 50", ["info depth 1 score cp 15 wdl 10 980 10", "bestmove e2e4"])
    # This used to raise InvalidStateError because cancellation reached the UCI ready future.
    protocol.pipe_data_received(1, b"readyok\n")
    with pytest.raises(asyncio.CancelledError):
        await first
    transport.expect("ucinewgame")
    transport.expect("isready", ["readyok"])
    transport.expect("position startpos")
    transport.expect("go movetime 50", ["info depth 2 score cp 20 wdl 20 970 10", "bestmove d2d4"])
    result = await engine.evaluate(chess.Board(), "game0002")
    assert result.evaluation_cp == 20
    assert result.probabilities.model_dump() == {"white": 0.02, "draw": 0.97, "black": 0.01}
    transport.assert_done()
