import pytest

from onsite import bank
from onsite.committee import CommitteeNotes, Theme, decide, one_below, review
from onsite.grader import GradedQuestion, RoundReport, ScoredCriterion, recommend
from onsite.models import Level, Track


def round_report(track, score, level=Level.SENIOR):
    tb = bank.load(track)
    scores = [
        ScoredCriterion(criterion_id=c.id, name=c.name, score=score, reasoning=f"{c.name} reason", evidence=[])
        for c in tb.rubric
    ]
    q = tb.questions[0]
    graded = GradedQuestion(
        question_id=q.id, title=q.title, skipped=False, scores=scores,
        strengths=[f"{track} strength"], gaps=[f"{track} gap"], stronger_answer="",
    )
    means = {c.id: float(score) for c in tb.rubric}
    return RoundReport(
        track=track, level=level, questions=[graded], criterion_means=means,
        mean=float(score), recommendation=recommend(score, means),
    )


def notes(**overrides):
    values = dict(
        summary="Solid loop.",
        themes=[],
        strongest_signal="Clean code in round 1.",
        biggest_concern="Thin requirements in round 2.",
        level_call="at_level",
        focus=["Ask about scale first"],
    )
    return CommitteeNotes(**(values | overrides))


LOOP = [Track.CODING, Track.SYSTEM_DESIGN, Track.BEHAVIORAL]


def test_rounds_count_the_same():
    rounds = [round_report(t, s) for t, s in zip(LOOP, [4, 3, 3])]
    assert decide(rounds) == ("Hire", [])


def test_one_no_hire_blocks_a_hire_even_with_a_high_average():
    rounds = [round_report(t, s) for t, s in zip(LOOP, [4, 4, 2])]
    assert rounds[2].recommendation == "No Hire"
    assert decide(rounds) == ("Lean No Hire", [3])


def test_a_lean_no_hire_round_does_not_block():
    rounds = [round_report(Track.CODING, 4), round_report(Track.SYSTEM_DESIGN, 4)]
    rounds[1].recommendation = "Lean No Hire"
    assert decide(rounds) == ("Strong Hire", [])


def test_review_sends_the_whole_packet_and_the_decision(fake_llm):
    rounds = [round_report(t, 3) for t in LOOP]
    fake = fake_llm(notes())
    report = review(fake, Level.SENIOR, rounds)

    call = fake.calls[0]
    assert call["effort"] == "high"
    for i, track in enumerate(["coding", "system design", "behavioral"], 1):
        assert f"Round {i}: {track}. Interviewer's recommendation: Hire" in call["prompt"]
    assert "coding gap" in call["prompt"] and "Correctness (3): Correctness reason" in call["prompt"]
    assert "The committee's decision: Hire." in call["prompt"]
    assert "All 3 rounds were completed." in call["prompt"]
    assert bank.load(Track.SYSTEM_DESIGN).level_bar[Level.MID] in call["system"]
    assert report.decision == "Hire" and report.hire_level == Level.SENIOR and report.complete


def test_themes_must_name_two_real_rounds(fake_llm):
    themes = [
        Theme(pattern="Rushes past requirements", rounds=[1, 2, 2]),
        Theme(pattern="Only seen once", rounds=[3]),
        Theme(pattern="Names a round that never ran", rounds=[2, 5]),
    ]
    report = review(fake_llm(notes(themes=themes)), Level.SENIOR, [round_report(t, 3) for t in LOOP])
    assert report.themes == [Theme(pattern="Rushes past requirements", rounds=[1, 2])]


def test_focus_is_kept_to_three(fake_llm):
    report = review(fake_llm(notes(focus=list("abcde"))), Level.SENIOR, [round_report(Track.CODING, 3)])
    assert report.focus == ["a", "b", "c"]


def test_committee_can_offer_one_level_down(fake_llm):
    rounds = [round_report(t, 2) for t in LOOP]
    report = review(fake_llm(notes(level_call="one_level_down")), Level.SENIOR, rounds)
    assert report.decision == "No Hire"
    assert report.hire_level == Level.MID


def test_a_hire_is_always_at_the_level_applied_for(fake_llm):
    report = review(fake_llm(notes(level_call="one_level_down")), Level.SENIOR, [round_report(t, 3) for t in LOOP])
    assert report.hire_level == Level.SENIOR


def test_there_is_no_level_below_junior(fake_llm):
    rounds = [round_report(t, 2, Level.JUNIOR) for t in LOOP]
    fake = fake_llm(notes(level_call="one_level_down"))
    report = review(fake, Level.JUNIOR, rounds)
    assert report.hire_level is None
    assert "There is no lower level" in fake.calls[0]["system"]


def test_an_unfinished_loop_says_so(fake_llm):
    fake = fake_llm(notes())
    report = review(fake, Level.SENIOR, [round_report(Track.CODING, 3)], planned=3)
    assert "The candidate finished 1 of 3 rounds. Say so" in fake.calls[0]["prompt"]
    assert not report.complete


def test_a_round_stopped_part_way_is_graded_but_not_finished(fake_llm):
    fake = fake_llm(notes())
    rounds = [round_report(Track.CODING, 3), round_report(Track.BEHAVIORAL, 3)]
    report = review(fake, Level.SENIOR, rounds, planned=3, finished=1)
    assert "finished 1 of 3 rounds and stopped part way through round 2." in fake.calls[0]["prompt"]
    assert report.finished == 1 and not report.complete


def test_review_needs_a_round(fake_llm):
    with pytest.raises(ValueError):
        review(fake_llm(), Level.SENIOR, [])


def test_one_below():
    assert one_below(Level.STAFF) == Level.SENIOR
    assert one_below(Level.JUNIOR) is None
