package lila.round

import scala.collection.mutable

import play.api.libs.json.*

/** Share inference for a position while keeping each subscriber's request identity and cancellation. */
final class CommentaryRequests(
    process: JsObject => Fu[JsObject],
    cancel: String => Unit
):
  private case class Entry(upstreamId: String, result: Fu[JsObject], subscribers: mutable.Set[String])
  private val positions = mutable.Map.empty[JsObject, Entry]
  private val subscriptions = mutable.Map.empty[String, JsObject]
  private val identityFields = Set("requestId", "gameRevision", "acceptedAt", "expiresAt")

  def apply(job: JsObject)(using Executor): Fu[JsObject] = synchronized:
    val id = (job \ "requestId").as[String]
    val position = JsObject(job.fields.filterNot((key, _) => identityFields(key)))
    val entry = positions.getOrElseUpdate(position, Entry(id, process(job), mutable.Set.empty))
    entry.subscribers.add(id)
    subscriptions.update(id, position)
    val identity = JsObject(job.fields.filter((key, _) => identityFields(key)))
    entry.result.map(_ ++ identity)

  def cancelRequest(id: String): Unit = synchronized:
    subscriptions
      .remove(id)
      .foreach: position =>
        positions
          .get(position)
          .foreach: entry =>
            entry.subscribers.remove(id)
            if entry.subscribers.isEmpty then
              positions.remove(position)
              cancel(entry.upstreamId)
