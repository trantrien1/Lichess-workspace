package lila.dailyGoal
package ui

import java.time.LocalDate
import java.time.format.TextStyle

import play.api.data.Form

import lila.ui.*
import lila.ui.ScalatagsTemplate.{ *, given }

final class DailyGoalUi(helpers: Helpers)(
    AccountPage: (String, String) => Context ?=> Page
):
  import helpers.{ *, given }

  def index(goal: Option[DailyGoal], form: Form[?])(using Context) =
    AccountPage("Daily goal", "daily-goal"):
      // a new goal starts in the browser's time zone, see user.account.ts
      div(cls := List("daily-goal box box-pad" -> true, "daily-goal--new" -> goal.isEmpty))(
        h1(cls := "box__top")("Daily goal"),
        standardFlash,
        goal.map(summary),
        p(cls := "daily-goal__intro")(
          "Choose how many puzzles and games you want to finish each day. ",
          "Reach the goal every day to grow your streak; miss a day and it starts again from zero. ",
          "A puzzle counts the first time you finish it, solved or not; a game counts when it ends."
        ),
        postForm(cls := "form3", action := routes.DailyGoal.save)(
          form3.globalError(form),
          form3.split(
            form3.group(form("puzzles"), "Puzzles per day", half = true):
              form3.select(_, DailyGoalForm.puzzleChoices.map(n => n.toString -> n.toString))
            ,
            form3.group(form("games"), "Games per day", half = true):
              form3.select(_, DailyGoalForm.gameChoices.map(n => n.toString -> n.toString))
          ),
          form3.group(
            form("zone"),
            "Time zone",
            help = frag("Your day ends at midnight in this time zone.").some
          ):
            form3.select(_, timeZone.translatedChoices)
          ,
          form3.action(form3.submit(if goal.isDefined then "Save" else "Start"))
        ),
        goal.isDefined.option(
          postForm(cls := "daily-goal__off", action := routes.DailyGoal.disable)(
            submitButton(
              cls := "button button-red button-thin yes-no-confirm",
              title := "Turn off your daily goal? Your streak will be lost."
            )("Turn off and reset my streak")
          )
        )
      )

  private def summary(goal: DailyGoal)(using Context) =
    val today = goal.today(nowInstant)
    val streak = goal.currentStreak(today)
    div(cls := "daily-goal__summary")(
      div(cls := "daily-goal__streak", dataIcon := Icon.Fire)(
        strong(streak),
        span("day streak"),
        span(cls := "daily-goal__best")("Best: ", days(goal.best))
      ),
      tasks(goal, today),
      div(cls := "daily-goal__week"):
        goal
          .week(today)
          .map: (day, done) =>
            div(cls := List("daily-goal__day" -> true, "done" -> done, "today" -> (day == today)))(
              span(cls := "daily-goal__day__mark", done.option(dataIcon := Icon.Checkmark)),
              span(cls := "daily-goal__day__name")(weekday(day))
            )
    )

  /** The goal and streak on the homepage, or an invitation to set one. */
  def home(goal: Option[DailyGoal]) =
    goal match
      case None =>
        a(
          cls := "daily-goal-home daily-goal-home--off",
          href := routes.DailyGoal.index,
          dataIcon := Icon.Fire
        )(
          span(
            strong("Set a daily goal"),
            span("Solve puzzles and play every day to build a streak")
          )
        )
      case Some(goal) =>
        val today = goal.today(nowInstant)
        val streak = goal.currentStreak(today)
        val done = goal.isDone(today)
        a(
          cls := List("daily-goal-home" -> true, "daily-goal-home--done" -> done),
          href := routes.DailyGoal.index
        )(
          div(cls := "daily-goal-home__streak", dataIcon := Icon.Fire)(
            strong(streak),
            span("day streak")
          ),
          tasks(goal, today),
          div(cls := "daily-goal-home__status")(
            if done then "Goal reached today. See you tomorrow!"
            else if streak > 0 then "Reach today's goal to keep your streak."
            else "Reach today's goal to start a streak."
          )
        )

  private def tasks(goal: DailyGoal, today: LocalDate) =
    val p = goal.progress(today)
    div(cls := "daily-goal-tasks")(
      (goal.puzzles > 0).option(task("Puzzles", p.puzzles, goal.puzzles)),
      (goal.games > 0).option(task("Games", p.games, goal.games))
    )

  private def task(name: String, nb: Int, target: Int) =
    val shown = nb.min(target)
    div(cls := List("daily-goal-tasks__task" -> true, "done" -> (nb >= target)))(
      span(cls := "daily-goal-tasks__name")(name),
      span(cls := "daily-goal-tasks__count")(s"$shown / $target"),
      span(cls := "daily-goal-tasks__bar")(span(style := s"width:${shown * 100 / target}%"))
    )

  private def days(nb: Int) = if nb == 1 then "1 day" else s"$nb days"

  private def weekday(day: LocalDate)(using ctx: Context) =
    day.getDayOfWeek.getDisplayName(TextStyle.SHORT, ctx.lang.locale)
