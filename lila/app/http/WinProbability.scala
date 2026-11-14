package lila.app
package http

import play.api.Configuration
import play.api.libs.json.*
import play.api.libs.ws.StandaloneWSClient
import play.api.libs.ws.JsonBodyWritables.*

import lila.common.Json.given

/** Demand-driven WDL for spectators. The game and its SAN history always come from Lila. */
final class WinProbability(config: Configuration, ws: StandaloneWSClient, cacheApi: lila.memo.CacheApi)(using
    Executor
):
  val enabled = config.getOptional[Boolean]("winProbability.enabled").getOrElse(false)
  private val endpoint = config.getOptional[String]("winProbability.url").getOrElse("http://prediction:8000")

  // Coalesce spectator HTTP requests as well as engine jobs; keep the number of entries bounded.
  private val cache = cacheApi.notLoading[(GameId, String), JsObject](64, "winProbability"):
    _.maximumSize(1024).expireAfterWrite(150.millis).buildAsync()

  def apply(game: GameModel): Fu[JsObject] =
    val revision =
      s"${game.id.value}|${game.sans.map(_.value).mkString(" ")}|${game.status.id}|${game.winnerColor.fold("")(_.name)}"
    val identity = Json.obj("game_id" -> game.id.value, "revision" -> revision)
    if game.aborted then fuccess(identity ++ Json.obj("status" -> "aborted", "source" -> "official"))
    else if game.finished then
      fuccess(
        identity ++ Json.obj(
          "status" -> "finished",
          "source" -> "official",
          "probabilities" -> Json.obj(
            "white" -> (if game.winnerColor.contains(Color.white) then 1 else 0),
            "draw" -> (if game.winnerColor.isEmpty then 1 else 0),
            "black" -> (if game.winnerColor.contains(Color.black) then 1 else 0)
          )
        )
      )
    else
      cache.getFuture(
        (game.id, revision),
        _ =>
          ws.url(s"$endpoint/v1/predictions")
            .withRequestTimeout(800.millis)
            .post(
              Json.obj(
                "game_id" -> game.id.value,
                "moves" -> game.sans.map(_.value),
                "context" -> game.clock.map: clock =>
                  Json.obj(
                    "white_rating" -> game.whitePlayer.rating.map(_.value),
                    "black_rating" -> game.blackPlayer.rating.map(_.value),
                    "white_clock" -> math.max(0d, clock.remainingTime(Color.white).toSeconds.toDouble),
                    "black_clock" -> math.max(0d, clock.remainingTime(Color.black).toSeconds.toDouble),
                    "initial_seconds" -> clock.limitSeconds.value,
                    "increment_seconds" -> clock.incrementSeconds.value,
                    "speed" -> game.speed.key
                  )
              )
            )
            .map: response =>
              require(response.status == 200, "WDL service unavailable")
              val body = Json.parse(response.body).as[JsObject]
              require((body \ "game_id").as[String] == game.id.value, "WDL game mismatch")
              identity ++ body
            .recover:
              case _: Exception => identity ++ Json.obj("status" -> "unavailable", "source" -> "engine")
      )
