import os
from datetime import datetime

from onsite.committee import LoopReport
from onsite.history import load
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
