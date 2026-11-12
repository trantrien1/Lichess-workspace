package controllers

import java.util.concurrent.ConcurrentHashMap
import scala.concurrent.Promise

import org.apache.pekko.stream.scaladsl.Source

import play.api.libs.EventSource
import play.api.libs.json.*
import play.api.libs.ws.{ StandaloneWSClient, JsonBodyWritables }

import lila.app.*

/** Opt-in commentary for human players of standard games. */
final class Commentary(env: Env, ws: StandaloneWSClient)
    extends LilaController(env)
    with lila.web.TheftPrevention:
  import JsonBodyWritables.*

  private val enabled = env.config.getOptional[Boolean]("commentary.enabled").getOrElse(false)
  private val endpoint = env.config.getOptional[String]("commentary.endpoint").getOrElse("")
  private val token = env.config.getOptional[String]("commentary.token").getOrElse("")
  private val connections = new ConcurrentHashMap[String, Promise[JsObject]]()
  private val superseded = Json.obj("type" -> "superseded")
  private val voices = Set("piper-vais1000")

  private val requests = new lila.round.CommentaryRequests(
    job =>
      ws.url(s"$endpoint/comment")
        .addHttpHeaders("Authorization" -> s"Bearer $token")
        .withRequestTimeout(12.seconds)
        .post(job)
        .map: response =>
          if response.status != 200 then
            lila.round.logger.warn(s"Commentary service HTTP ${response.status}")
            throw RuntimeException("commentary unavailable")
          Json.parse(response.body).as[JsObject]
    ,
    requestId =>
      ws.url(s"$endpoint/cancel/$requestId")
        .addHttpHeaders("Authorization" -> s"Bearer $token")
        .withRequestTimeout(2.seconds)
        .post(Json.obj())
        .recover { case _ => () }
      ()
  )

  def stream(gameId: GameId, voice: String) = Open { ctx ?=>
    if !enabled || token.isEmpty then notFound
    else if !voices(voice) then fuccess(BadRequest)
    else
      Found(env.round.proxyRepo.game(gameId)): game =>
        val player = game.players.find(p => isMyPov(Pov(game, p)))
        if game.variant != chess.variant.Standard || player.isEmpty then fuccess(Forbidden)
        else
          val key = ctx.me.fold(s"anon:${game.id}:${player.get.id}")(_.userId.value)
          val takeover = Promise[JsObject]()
          val permitted = connections.synchronized:
            if connections.size >= 4 && !connections.containsKey(key) then false
            else
              Option(connections.put(key, takeover)).foreach(_.trySuccess(superseded))
              true
          if !permitted then fuccess(TooManyRequests)
          else
            val source = env.round.commentaryStream(game, voice)(requests.apply, requests.cancelRequest)
            fuccess:
              Ok.chunked(
                source
                  // Tell the displaced browser to CLOSE its EventSource. EOF
                  // alone makes both tabs reconnect and steal the stream back.
                  .merge(Source.future(takeover.future))
                  .takeWhile(json => (json \ "type").asOpt[String] != Some("superseded"), inclusive = true)
                  .keepAlive(10.seconds, () => Json.obj("type" -> "heartbeat"))
                  .watchTermination(): (_, done) =>
                    done.addEffectAnyway:
                      connections.remove(key, takeover)
                      takeover.trySuccess(superseded)
                      ()
                  .via(EventSource.flow)
              ).as("text/event-stream")
                .noProxyBuffer
                .noCache
  }
