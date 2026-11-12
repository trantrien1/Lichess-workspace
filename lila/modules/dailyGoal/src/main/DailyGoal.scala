package lila.dailyGoal

import java.time.{ Instant, LocalDate, ZoneId }

enum Task:
  case puzzle, game

/** A player's daily target (puzzles and games) and the streak of days they reached it. Days follow the
  * player's own time zone, so a day ends at their local midnight.
  */
case class DailyGoal(
    _id: UserId,
    puzzles: Int,
    games: Int,
    zone: ZoneId,
    day: LocalDate, // the day nbPuzzles and nbGames count for
    nbPuzzles: Int,
    nbGames: Int,
    streak: Int,
    best: Int,
    lastDone: Option[LocalDate],
    doneDays: List[LocalDate] // most recent first
):
  import DailyGoal.*

  inline def userId = _id

  def today(now: Instant): LocalDate = now.atZone(zone).toLocalDate

  def progress(on: LocalDate): Progress =
    if day == on then Progress(nbPuzzles, nbGames) else Progress(0, 0)

  def isDone(on: LocalDate) = lastDone.contains(on)

  /** A streak lives until the end of the day after the last completed day. */
  def currentStreak(on: LocalDate): Int =
    if lastDone.exists(!_.isBefore(on.minusDays(1))) then streak else 0

  /** The last 7 days, oldest first, and whether the goal was reached on each. */
  def week(on: LocalDate): List[(LocalDate, Boolean)] =
    (6 to 0 by -1).toList.map: ago =>
      val d = on.minusDays(ago)
      d -> doneDays.contains(d)

  def record(task: Task, now: Instant): DailyGoal =
    val on = today(now)
    val p = progress(on)
    val counted = task match
      case Task.puzzle => copy(day = on, nbPuzzles = p.puzzles + 1, nbGames = p.games)
      case Task.game => copy(day = on, nbPuzzles = p.puzzles, nbGames = p.games + 1)
    counted.complete(on)

  def withSettings(puzzles: Int, games: Int, zone: ZoneId, now: Instant): DailyGoal =
    val updated = copy(puzzles = puzzles, games = games, zone = zone)
    updated.complete(updated.today(now))

  /** Marks the day done once both targets are reached, extending the streak or starting a new one. */
  private def complete(on: LocalDate): DailyGoal =
    val p = progress(on)
    // a later lastDone happens when the player moves to an earlier time zone
    if lastDone.exists(!_.isBefore(on)) || p.puzzles < puzzles || p.games < games then this
    else
      val next = if lastDone.contains(on.minusDays(1)) then streak + 1 else 1
      copy(
        streak = next,
        best = best.max(next),
        lastDone = on.some,
        doneDays = (on :: doneDays).take(keepDays)
      )

object DailyGoal:

  case class Progress(puzzles: Int, games: Int)

  private val keepDays = 31

  def make(userId: UserId, puzzles: Int, games: Int, zone: ZoneId, now: Instant): DailyGoal =
    val start = now.atZone(zone).toLocalDate
    DailyGoal(userId, 0, 0, zone, start, 0, 0, 0, 0, None, Nil).withSettings(puzzles, games, zone, now)
