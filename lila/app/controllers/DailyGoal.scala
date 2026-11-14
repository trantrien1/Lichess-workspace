package controllers

import lila.app.*
import lila.dailyGoal.DailyGoalForm

final class DailyGoal(env: Env) extends LilaController(env):

  def index = Auth { _ ?=> me ?=>
    for
      goal <- env.dailyGoal.api.get(me)
      page <- Ok.page(views.dailyGoal.index(goal, goal.fold(DailyGoalForm.default)(DailyGoalForm.of)))
    yield page
  }

  def save = AuthBody { _ ?=> me ?=>
    bindForm(DailyGoalForm.form)(
      err => env.dailyGoal.api.get(me).flatMap(goal => BadRequest.page(views.dailyGoal.index(goal, err))),
      data => env.dailyGoal.api.setGoal(me, data).inject(Redirect(routes.DailyGoal.index).flashSuccess)
    )
  }

  def disable = Auth { _ ?=> me ?=>
    env.dailyGoal.api.disable(me).inject(Redirect(routes.DailyGoal.index).flashSuccess)
  }
