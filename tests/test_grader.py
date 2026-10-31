import pytest

from onsite import bank
from onsite.grader import CriterionGrade, grade_exchange, grade_round, grade_schema, quote_found, recommend
from onsite.models import Exchange, Level, Track, Turn

ANSWER = (
    "I owned the migration from MySQL to Postgres. I wrote the dual-write plan myself and "
    "we cut p99 latency from 800ms to 120ms."
)


def exchange(answer=ANSWER, skipped=False):
    tb = bank.load(Track.BEHAVIORAL)
    ex = Exchange(question=tb.questions[0], skipped=skipped)
    ex.turns.append(Turn(speaker="interviewer", text=ex.question.prompt))
    if answer:
        ex.turns.append(Turn(speaker="candidate", text=answer))
    return ex


def model_reply(evidence, score=3):
    tb = bank.load(Track.BEHAVIORAL)
    schema = grade_schema(tb.track, tuple(c.id for c in tb.rubric))
    grade = CriterionGrade(evidence=evidence, reasoning="because", score=score)
    return schema(
        scores={c.id: grade for c in tb.rubric},
        strengths=["clear numbers"],
        gaps=["no reflection"],
        stronger_answer="Say what you learned.",
    )


def test_exact_quote_is_found():
    assert quote_found("I wrote the dual-write plan myself", ANSWER)


def test_quote_survives_small_punctuation_changes():
    assert quote_found("we cut p99 latency from 800 ms to 120ms", ANSWER)


def test_made_up_quote_is_rejected():
    assert not quote_found("I mentored three junior engineers through the launch", ANSWER)


def test_tiny_quotes_do_not_count():
    assert not quote_found("I", ANSWER)


def test_real_evidence_keeps_the_score(fake_llm):
    llm = fake_llm(model_reply(["I owned the migration from MySQL to Postgres"], score=4))
    graded = grade_exchange(llm, bank.load(Track.BEHAVIORAL), Level.SENIOR, exchange())
    assert all(s.score == 4 and not s.capped for s in graded.scores)
    assert llm.calls[0]["effort"] == "high"


def test_invented_evidence_gets_no_credit(fake_llm):
    llm = fake_llm(model_reply(["I led a team of twenty people across three offices"], score=4))
    graded = grade_exchange(llm, bank.load(Track.BEHAVIORAL), Level.SENIOR, exchange())
    for s in graded.scores:
        assert s.score == 1
        assert s.capped
        assert s.rejected_quotes == ["I led a team of twenty people across three offices"]


def test_interviewer_words_are_not_evidence(fake_llm):
    tb = bank.load(Track.BEHAVIORAL)
    llm = fake_llm(model_reply([tb.questions[0].prompt], score=3))
    graded = grade_exchange(llm, tb, Level.SENIOR, exchange())
    assert all(s.capped for s in graded.scores)


def test_skipped_question_scores_one_without_calling_the_model(fake_llm):
    llm = fake_llm()
    graded = grade_exchange(llm, bank.load(Track.BEHAVIORAL), Level.SENIOR, exchange(skipped=True))
    assert graded.skipped
    assert all(s.score == 1 for s in graded.scores)
    assert llm.calls == []


def test_round_report_averages_and_recommends(fake_llm):
    quote = ["I wrote the dual-write plan myself"]
    llm = fake_llm(model_reply(quote, score=4), model_reply(quote, score=3))
    report = grade_round(llm, bank.load(Track.BEHAVIORAL), Level.SENIOR, [exchange(), exchange()])
    assert report.mean == pytest.approx(3.5)
    assert report.recommendation == "Strong Hire"
    assert set(report.criterion_means) == {c.id for c in bank.load(Track.BEHAVIORAL).rubric}


@pytest.mark.parametrize(
    "overall, expected",
    [(3.6, "Strong Hire"), (3.1, "Hire"), (2.8, "Lean Hire"), (2.5, "Lean No Hire"), (2.0, "No Hire"), (1.2, "Strong No Hire")],
)
def test_recommendation_bands(overall, expected):
    assert recommend(overall, {"a": overall}) == expected


def test_red_flag_blocks_a_hire():
    assert recommend(3.2, {"a": 4.0, "b": 4.0, "c": 1.5}) == "Lean No Hire"
