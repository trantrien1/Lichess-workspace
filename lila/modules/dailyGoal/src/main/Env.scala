package lila.dailyGoal

import com.softwaremill.macwire.*

import lila.common.Bus
import lila.core.config.CollName

@Module
final class Env(db: lila.db.Db)(using Executor, Scheduler):

  private val coll = db(CollName("daily_goal"))

  val api = wire[DailyGoalApi]

  // a puzzle counts the first time the player finishes it, solved or not
  Bus.sub[lila.puzzle.Puzzle.UserResult]: res =>
    api.record(res.userId, Task.puzzle)

  Bus.sub[lila.core.game.FinishGame]:
    case lila.core.game.FinishGame(game, _) if !game.aborted =>
      game.userIds.foreach(api.record(_, Task.game))

  Bus.sub[lila.core.user.UserDelete]: del =>
    api.disable(del.id)
