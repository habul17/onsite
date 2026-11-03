from datetime import datetime

from onsite import bank
from onsite.committee import LoopReport, Theme
from onsite.grader import grade_exchange, grade_round
from onsite.models import Exchange, Level, Question, Track, Turn
from onsite.report import loop_to_markdown, to_markdown
from test_cli import grade
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


def test_round_report_shows_the_resume_line_behind_a_question(fake_llm):
    tb = bank.load(Track.BEHAVIORAL)
    question = Question(
        id="resume-1",
        track=Track.BEHAVIORAL,
        title="Ledger migration",
        prompt="Tell me about the ledger migration.",
        strong_signals=["specific"],
        based_on="Led the migration of the ledger",
    )
    exchange = Exchange(question=question)
    exchange.turns += [
        Turn(speaker="interviewer", text=question.prompt),
        Turn(speaker="candidate", text="I wrote the migration plan myself and ran it over a weekend."),
    ]
    report = grade_round(fake_llm(grade(3)), tb, Level.MID, [exchange])

    assert report.questions[0].based_on == "Led the migration of the ledger"
    text = to_markdown(report, [exchange])
    assert '## Ledger migration\n\n*From your resume: "Led the migration of the ledger"*' in text


def test_bank_questions_have_no_resume_line(fake_llm):
    tb = bank.load(Track.BEHAVIORAL)
    graded = grade_exchange(fake_llm(), tb, Level.MID, Exchange(question=tb.questions[0], skipped=True))
    assert graded.based_on is None
