package lila.round

import java.util.UUID

import org.apache.pekko.actor.Cancellable
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
)(using Executor, Scheduler, org.apache.pekko.stream.Materializer):

  import CommentaryStream.*

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
        // Remarks made while the player to move is thinking, since the last move.
        var idleCount = 0
        var idleTimer: Option[Cancellable] = None
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
                      // Engine scores/PVs must never reach a player's browser mid-game.
                      output.offer((result - "facts") ++ Json.obj("subscriptionId" -> subscriptionId))
                  ()
          .toMat(Sink.ignore)(Keep.left)
          .run()

        // Called under the lock: a new request replaces the one still running.
        def nextRequest(g: Game): JsObject =
          revision += 1
          if currentId.nonEmpty then cancelRequest(currentId)
          currentId = s"$subscriptionId:$revision"
          val acceptedAt = nowMillis
          Json.obj(
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

        def job(base: JsObject, g: Game, event: String): JsObject =
          // The SAN history is authoritative and preserves repetition rights.
          (base - "subscriptionId") ++ Json.obj(
            "initialFen" -> Fen.initial.value,
            "sanHistory" -> g.sans,
            "event" -> event,
            "voice" -> voice
          )

        def thinking(g: Game) = g.playable && g.status.name == "started"

        def scheduleIdle(g: Game): Unit =
          idleTimer.foreach(_.cancel())
          idleTimer =
            if closed || !thinking(g) || idleCount >= idleMax then None
            else Some(summon[Scheduler].scheduleOnce(if idleCount == 0 then idleFirst else idleEvery)(idle()))

        // Nobody has moved for a while: a commentator still talks, about the clock, material, the last move.
        def idle(): Unit = lock.synchronized:
          latest
            .filter(g => !closed && initialized && thinking(g))
            .foreach: g =>
              idleCount += 1
              val base = nextRequest(g)
              val acceptedAt = (base \ "acceptedAt").as[Long]
              val clock = g.clock.fold(Json.obj()) { c =>
                def seconds(color: Color) = (c.remainingTime(color).millis / 1000).max(0L)
                Json.obj(
                  "clock" -> Json.obj(
                    "white" -> seconds(Color.White),
                    "black" -> seconds(Color.Black),
                    "running" -> c.isRunning
                  )
                )
              }
              output.offer(
                base ++ Json.obj("type" -> "state", "snapshot" -> false, "reset" -> false, "idle" -> true)
              )
              pending.offer(
                job(base, g, "idle") ++ clock ++ Json.obj(
                  "idleCount" -> idleCount,
                  "thinkingMs" -> (acceptedAt - g.movedAt.toMillis).max(0L).min(86_400_000L)
                )
              )
              scheduleIdle(g)

        def update(g: Game, initial: Boolean = false, reset: Boolean = false): Unit = lock.synchronized:
          if !closed && (initial || reset || latest.forall(x => x.sans != g.sans || x.status != g.status))
          then
            latest = Some(g)
            val base = nextRequest(g)
            output.offer(base ++ Json.obj("type" -> "state", "snapshot" -> initial, "reset" -> reset))
            if !reset && (!initial || (g.ply.value == 0 && g.status.name == "started")) then
              pending.offer(job(base, g, if initial then "intro" else "move"))
            idleCount = 0
            scheduleIdle(g)

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
              idleTimer.foreach(_.cancel())
              idleTimer = None
              if currentId.nonEmpty then cancelRequest(currentId)
              currentId = ""
              cancel.shutdown()
              pending.complete()
            Bus.unsubscribeDyn(moves, List(moveChan))
            Bus.unsubscribeDyn(takebacks, List(takebackChan))
            Bus.unsub[FinishGame](finishes)

object CommentaryStream:
  // While a player thinks: a first remark after 25 s, a second 30 s later, no more for that position.
  // A remark every few moves felt like chatter; most moves are made well within 25 s.
  val idleFirst = 25.seconds
  val idleEvery = 30.seconds
  val idleMax = 2
