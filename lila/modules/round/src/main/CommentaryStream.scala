package lila.round

import java.util.UUID

import org.apache.pekko.stream.{ KillSwitches, OverflowStrategy }
import org.apache.pekko.stream.scaladsl.*
import chess.format.Fen
import play.api.libs.json.*

import lila.common.Bus
import lila.common.Json.given
import lila.core.game.FinishGame
import lila.game.actorApi.{ MoveGameEvent, BoardTakeback }

/** Opt-in, bounded subscription. Never waits for inference in a game actor. */
final class CommentaryStream(
    getGame: GameId => Fu[Option[Game]]
)(using Executor, org.apache.pekko.stream.Materializer):

  def apply(game: Game, voice: String)(
      process: JsObject => Fu[JsObject],
      cancelRequest: String => Unit
  ): Source[JsObject, ?] =
    Source
      .queue[JsObject](8, OverflowStrategy.fail)
      .mapMaterializedValue: output =>
        val lock = new Object
        val subscriptionId = UUID.randomUUID().toString
        var revision = 0L
        var closed = false
        var initialized = false
        var latest: Option[Game] = None
        var currentId = ""
        val (pending, cancel) = Source
          .queue[JsObject](1, OverflowStrategy.dropHead)
          .viaMat(KillSwitches.single)(Keep.both)
          .mapAsync(1): job =>
            val id = (job \ "requestId").as[String]
            val active = lock.synchronized(!closed && currentId == id)
            if !active then fuccess(())
            else
              process(job)
                .recover: error =>
                  logger.warn(s"Commentary request failed (${error.getClass.getSimpleName})")
                  job ++ Json.obj("type" -> "unavailable", "reason" -> "service_unavailable")
                .map: result =>
                  lock.synchronized:
                    if !closed && currentId == id && (job \ "expiresAt").as[Long] > nowMillis then
                      output.offer(result ++ Json.obj("subscriptionId" -> subscriptionId))
                  ()
          .toMat(Sink.ignore)(Keep.left)
          .run()

        def update(g: Game, initial: Boolean = false, reset: Boolean = false): Unit = lock.synchronized:
          if !closed && (initial || reset || latest.forall(x => x.sans != g.sans || x.status != g.status))
          then
            latest = Some(g)
            revision += 1
            if currentId.nonEmpty then cancelRequest(currentId)
            currentId = s"$subscriptionId:$revision"
            val acceptedAt = nowMillis
            val base = Json.obj(
              "schemaVersion" -> 1,
              "subscriptionId" -> subscriptionId,
              "requestId" -> currentId,
              "gameId" -> g.id,
              "gameRevision" -> revision,
              "ply" -> g.ply.value,
              "fenAfter" -> Fen.write(g.chess).value,
              "status" -> g.status.name,
              "winner" -> g.winnerColor.map(_.name),
              "acceptedAt" -> acceptedAt,
              "expiresAt" -> (acceptedAt + 12000)
            )
            output.offer(base ++ Json.obj("type" -> "state", "snapshot" -> initial, "reset" -> reset))
            if !reset && (!initial || (g.ply.value == 0 && g.status.name == "started")) then
              // The SAN history is authoritative and preserves repetition rights.
              pending.offer(
                (base - "subscriptionId") ++ Json.obj(
                  "initialFen" -> Fen.initial.value,
                  "sanHistory" -> g.sans,
                  "event" -> (if initial then "intro" else "move"),
                  "voice" -> voice
                )
              )

        // Subscribe first; fetch a fresh proxy snapshot afterwards. Events seen
        // while fetching become the snapshot, never an audio backlog.
        def event(g: Game, reset: Boolean = false): Unit = lock.synchronized:
          if initialized then update(g, reset = reset)
          else latest = Some(g)

        val moveChan = MoveGameEvent.makeChan(game.id)
        val takebackChan = BoardTakeback.makeChan(game.id)
        val moves = Bus.subscribeFunDyn(moveChan):
          case MoveGameEvent(g, _, _) => event(g)
        val takebacks = Bus.subscribeFunDyn(takebackChan):
          case BoardTakeback(g) => event(g, reset = true)
        val finishes = Bus.sub[FinishGame]:
          case FinishGame(g, _) if g.id == game.id => event(g)

        getGame(game.id)
          .onComplete:
            case scala.util.Success(snapshot) =>
              lock.synchronized:
                val fresh = latest.orElse(snapshot).getOrElse(game)
                initialized = true
                update(fresh, initial = true)
            case scala.util.Failure(e) => output.fail(e)

        output
          .watchCompletion()
          .addEffectAnyway:
            lock.synchronized:
              closed = true
              if currentId.nonEmpty then cancelRequest(currentId)
              currentId = ""
              cancel.shutdown()
              pending.complete()
            Bus.unsubscribeDyn(moves, List(moveChan))
            Bus.unsubscribeDyn(takebacks, List(takebackChan))
            Bus.unsub[FinishGame](finishes)
