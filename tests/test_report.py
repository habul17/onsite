from datetime import datetime

from onsite.committee import LoopReport, Theme
from onsite.models import Level, Track
from onsite.report import loop_to_markdown
from test_committee import round_report


def loop(**overrides):
    rounds = [round_report(Track.CODING, 4), round_report(Track.SYSTEM_DESIGN, 2)]
    values = dict(
        level=Level.SENIOR,
        planned=2,
        finished=2,
        rounds=rounds,
        mean=3.0,
        decision="Lean No Hire",
        blocked_by=[2],
        hire_level=Level.MID,
        summary="Strong coder, but the design round was a clear miss.",
        themes=[Theme(pattern="Jumps into details before the big picture", rounds=[1, 2])],
        strongest_signal="Clean, tested code in round 1.",
        biggest_concern="No capacity estimate in round 2.",
        focus=["Estimate scale before drawing boxes"],
    )
    return LoopReport(**(values | overrides))


def test_loop_report_has_the_decision_rounds_and_next_steps():
    text = loop_to_markdown(loop(), ["1-coding.md", "2-system_design.md"], datetime(2026, 11, 2, 9, 30))

    assert text.startswith("# Onsite loop, senior level\n\n2026-11-02 09:30\n")
    assert "**Decision: Lean No Hire** (average 3.00 / 4)" in text
    assert "would hire at **mid** level instead" in text
    assert "| [1. Coding](1-coding.md) | Strong Hire | 4.00 |" in text
    assert "| [2. System design](2-system_design.md) | No Hire (blocked a hire) | 2.00 |" in text
    assert "- Jumps into details before the big picture (rounds 1, 2)" in text
    assert "1. Estimate scale before drawing boxes" in text
    assert "Unfinished" not in text


def test_unfinished_loop_is_flagged_and_hire_at_level_is_not_repeated():
    text = loop_to_markdown(loop(planned=3, decision="Hire", blocked_by=[], hire_level=Level.SENIOR, themes=[]))
    assert "*Unfinished loop: 2 of 3 rounds finished." in text
    assert "instead" not in text
    assert "Patterns across rounds" not in text
    assert "| 1. Coding | Strong Hire | 4.00 |" in text
