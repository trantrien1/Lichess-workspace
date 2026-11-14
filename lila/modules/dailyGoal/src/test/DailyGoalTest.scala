package lila.dailyGoal

import java.time.{ Instant, LocalDate, LocalTime, ZoneId }

class DailyGoalTest extends munit.FunSuite:

  val hanoi = ZoneId.of("Asia/Ho_Chi_Minh")
  val monday = LocalDate.of(2026, 11, 9)
  def at(day: LocalDate, hour: Int = 12): Instant = day.atTime(LocalTime.of(hour, 0)).atZone(hanoi).toInstant
  def day(n: Int) = monday.plusDays(n)

  // 2 puzzles and 1 game a day, set on Monday morning
  val fresh = DailyGoal.make(UserId("alice"), puzzles = 2, games = 1, hanoi, at(monday, 8))

  def finishDay(goal: DailyGoal, on: LocalDate) =
    goal.record(Task.puzzle, at(on)).record(Task.puzzle, at(on)).record(Task.game, at(on))

  test("the day is done once every target is reached"):
    val g = fresh.record(Task.puzzle, at(monday)).record(Task.game, at(monday))
    assertEquals(g.progress(monday), DailyGoal.Progress(1, 1))
    assert(!g.isDone(monday))
    assertEquals(g.currentStreak(monday), 0)
    val done = g.record(Task.puzzle, at(monday))
    assert(done.isDone(monday))
    assertEquals(done.currentStreak(monday), 1)

  test("extra puzzles and games after the goal do not count twice"):
    val g = finishDay(fresh, monday).record(Task.puzzle, at(monday)).record(Task.game, at(monday))
    assertEquals(g.streak, 1)
    assertEquals(g.progress(monday), DailyGoal.Progress(3, 2))

  test("consecutive days grow the streak"):
    val g = (0 to 2).foldLeft(fresh)((g, n) => finishDay(g, day(n)))
    assertEquals(g.currentStreak(day(2)), 3)
    assertEquals(g.best, 3)

  test("progress starts from zero on a new day"):
    val g = finishDay(fresh, monday)
    assertEquals(g.progress(day(1)), DailyGoal.Progress(0, 0))
    assertEquals(g.record(Task.game, at(day(1))).progress(day(1)), DailyGoal.Progress(0, 1))

  test("the streak survives the next day and breaks after a missed day"):
    val g = finishDay(fresh, monday)
    assertEquals(g.currentStreak(day(1)), 1) // still time to play on Tuesday
    assertEquals(g.currentStreak(day(2)), 0) // Tuesday was missed
    val restarted = finishDay(g, day(2))
    assertEquals(restarted.currentStreak(day(2)), 1)
    assertEquals(restarted.best, 1)

  test("days end at local midnight, not UTC midnight"):
    // 23:30 in Hanoi is still Monday there, but already 16:30 UTC; 00:30 is Tuesday
    val g = finishDay(fresh, monday).record(Task.game, at(day(1), 0).minusSeconds(30 * 60))
    assertEquals(g.progress(monday), DailyGoal.Progress(2, 2))
    val tuesday = g.record(Task.game, at(day(1), 0).plusSeconds(30 * 60))
    assertEquals(tuesday.progress(day(1)), DailyGoal.Progress(0, 1))

  test("lowering the goal completes the day at once"):
    val g = fresh.record(Task.puzzle, at(monday)).record(Task.game, at(monday))
    assert(g.withSettings(puzzles = 1, games = 1, hanoi, at(monday)).isDone(monday))

  test("the week marks the completed days"):
    val g = finishDay(finishDay(fresh, monday), day(2))
    assertEquals(
      g.week(day(2)).map(_._2),
      List(false, false, false, false, true, false, true)
    )
