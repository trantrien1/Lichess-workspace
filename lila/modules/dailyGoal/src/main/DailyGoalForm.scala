package lila.dailyGoal

import java.time.ZoneId

import play.api.data.*
import play.api.data.Forms.*

import lila.common.Form.timeZone

object DailyGoalForm:

  case class Data(puzzles: Int, games: Int, zone: ZoneId)

  val puzzleChoices = List(0, 3, 5, 10, 15, 20, 30)
  val gameChoices = List(0, 1, 2, 3, 5)

  val form = Form(
    mapping(
      "puzzles" -> number.verifying(puzzleChoices.contains),
      "games" -> number.verifying(gameChoices.contains),
      "zone" -> timeZone.field
    )(Data.apply)(unapply)
      .verifying("Choose at least one puzzle or one game.", d => d.puzzles + d.games > 0)
  )

  // the time zone is filled in by the browser on the page
  val default = form.fill(Data(puzzles = 10, games = 1, zone = ZoneId.of("UTC")))

  def of(goal: DailyGoal) = form.fill(Data(goal.puzzles, goal.games, goal.zone))
