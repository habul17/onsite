import os
from datetime import datetime
from pathlib import Path

import pytest

from onsite import bank
from onsite.committee import LoopReport
from onsite.history import History, Session, Trend, focus, load, summarize
from onsite.models import Level, Track
from test_committee import round_report


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json())


def loop_report(rounds, when):
    return LoopReport(
        level=Level.SENIOR, planned=len(rounds), finished=len(rounds), rounds=rounds, mean=3.0, decision="Hire",
        blocked_by=[], hire_level=Level.SENIOR, summary="", themes=[], strongest_signal="", biggest_concern="",
        focus=[], taken_at=when,
    )


def test_loads_rounds_and_loops_oldest_first(tmp_path):
    newer = round_report(Track.CODING, 3)
    newer.taken_at = datetime(2026, 11, 3, 10, 0)
    save(tmp_path / "2026-11-03-1000-coding.json", newer)

    # Saved before reports had a time in them, so the time comes from the file name.
    save(tmp_path / "2026-10-31-0900-behavioral.json", round_report(Track.BEHAVIORAL, 2))

    loop_rounds = [round_report(Track.CODING, 4), round_report(Track.SYSTEM_DESIGN, 2)]
    folder = tmp_path / "2026-11-02-0800-loop"
    save(folder / "1-coding.json", loop_rounds[0])
    save(folder / "2-system_design.json", loop_rounds[1])
    save(folder / "loop.json", loop_report(loop_rounds, datetime(2026, 11, 2, 9, 0)))

    history = load(tmp_path)

    assert [(s.when, s.report.track, s.in_loop) for s in history.sessions] == [
        (datetime(2026, 10, 31, 9, 0), Track.BEHAVIORAL, False),
        (datetime(2026, 11, 2, 8, 0), Track.CODING, True),
        (datetime(2026, 11, 2, 8, 0), Track.SYSTEM_DESIGN, True),
        (datetime(2026, 11, 3, 10, 0), Track.CODING, False),
    ]
    assert [lp.when for lp in history.loops] == [datetime(2026, 11, 2, 9, 0)]
    assert history.unreadable == []


def test_a_loop_whose_committee_never_met_still_counts(tmp_path):
    save(tmp_path / "2026-11-02-0800-loop" / "1-coding.json", round_report(Track.CODING, 3))
    history = load(tmp_path)
    assert len(history.sessions) == 1 and history.loops == []


def test_files_that_are_not_reports_are_set_aside(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "notes.json").write_text('{"hello": "world"}')
    save(tmp_path / "2026-11-01-0900-coding.json", round_report(Track.CODING, 3))

    history = load(tmp_path)
    assert len(history.sessions) == 1
    assert sorted(p.name for p in history.unreadable) == ["broken.json", "notes.json"]


def test_a_renamed_report_falls_back_to_the_file_time(tmp_path):
    path = tmp_path / "my-best-round.json"
    save(path, round_report(Track.CODING, 3))
    os.utime(path, (datetime(2026, 10, 1).timestamp(),) * 2)
    assert load(tmp_path).sessions[0].when == datetime(2026, 10, 1)


def test_no_folder_means_no_history(tmp_path):
    history = load(tmp_path / "reports")
    assert history.sessions == [] and history.loops == [] and history.unreadable == []


def history_of(*rounds):
    """A History from (track, score) or (track, score, level) tuples, one day apart."""
    sessions = []
    for day, spec in enumerate(rounds, 1):
        track, score, *rest = spec
        level = rest[0] if rest else Level.SENIOR
        sessions.append(Session(datetime(2026, 10, day), round_report(track, score, level), Path(f"{day}.json"), False))
    return History(sessions=sessions)


def test_trend_compares_first_rounds_with_last_rounds_without_overlap():
    assert Trend("x", [3.0]).earlier is None and Trend("x", [3.0]).recent == 3.0
    two = Trend("x", [2.0, 3.0])
    assert (two.earlier, two.recent, two.change) == (2.0, 3.0, 1.0)
    five = Trend("x", [1.0, 2.0, 4.0, 3.0, 3.0])
    assert (five.earlier, five.recent) == (1.5, 3.0)
    many = Trend("x", [1, 1, 1, 2, 2, 3, 3, 4])
    assert (many.earlier, many.recent) == (1, pytest.approx(10 / 3))


def test_rounds_are_grouped_by_kind_and_level():
    history = history_of(
        (Track.CODING, 2), (Track.BEHAVIORAL, 3), (Track.CODING, 3), (Track.CODING, 4, Level.MID)
    )
    groups = summarize(history)
    assert [(g.track, g.level, len(g.sessions)) for g in groups] == [
        (Track.BEHAVIORAL, Level.SENIOR, 1),
        (Track.CODING, Level.MID, 1),
        (Track.CODING, Level.SENIOR, 2),
    ]
    coding = groups[2]
    assert coding.overall.scores == [2.0, 3.0]
    assert [t.name for t in coding.criteria] == [c.name for c in bank.load(Track.CODING).rubric]
    assert coding.criteria[0].scores == [2.0, 3.0]


def test_progress_can_be_filtered():
    history = history_of((Track.CODING, 2), (Track.BEHAVIORAL, 3), (Track.CODING, 3, Level.MID))
    assert [g.track for g in summarize(history, track=Track.CODING)] == [Track.CODING, Track.CODING]
    assert [g.level for g in summarize(history, level=Level.MID)] == [Level.MID]


def test_an_older_rubric_item_just_has_fewer_points():
    history = history_of((Track.CODING, 2), (Track.CODING, 3))
    del history.sessions[0].report.criterion_means["communication"]
    communication = summarize(history)[0].criteria[-1]
    assert communication.scores == [3.0]


def test_ready_means_the_bar_was_met_in_each_of_the_last_three_rounds():
    assert not summarize(history_of((Track.CODING, 3), (Track.CODING, 3)))[0].ready
    assert summarize(history_of((Track.CODING, 1), (Track.CODING, 3), (Track.CODING, 3), (Track.CODING, 4)))[0].ready
    assert not summarize(history_of((Track.CODING, 3), (Track.CODING, 2), (Track.CODING, 3)))[0].ready


def test_focus_picks_what_is_furthest_below_the_bar():
    history = history_of((Track.CODING, 3), (Track.CODING, 2), (Track.BEHAVIORAL, 4), (Track.BEHAVIORAL, 2))
    coding = history.sessions[1].report.criterion_means
    coding["complexity"] = 1.0
    picked = focus(summarize(history))
    assert (picked[0][0].track, picked[0][1].name) == (Track.CODING, "Complexity analysis")
    assert all(t.recent < 3 for _, t in picked) and len(picked) == 3
    # Behavioral fell from 4 to 2 while coding fell from 3 to 2, so behavioral items come first on a tie.
    assert picked[1][0].track == Track.BEHAVIORAL


def test_nothing_to_focus_on_when_everything_meets_the_bar():
    assert focus(summarize(history_of((Track.CODING, 3), (Track.CODING, 4)))) == []
