import pytest

from onsite import bank
from onsite.interviewer import Interview, InterviewerTurn
from onsite.models import Level, Track


def follow_up(message="What did you do yourself?"):
    return InterviewerTurn(action="continue", message=message, note="ownership unclear")


def move_on(message="Okay, thanks."):
    return InterviewerTurn(action="move_on", message=message, note="enough evidence")


def make(llm, count=2, max_follow_ups=3):
    tb = bank.load(Track.BEHAVIORAL)
    return Interview(llm, tb, Level.SENIOR, tb.questions[:count], max_follow_ups=max_follow_ups)


def test_start_asks_the_first_question(fake_llm):
    interview = make(fake_llm())
    opening = interview.start()
    assert interview.questions[0].prompt.strip() in opening
    assert len(interview.exchanges) == 1


def test_follow_up_stays_on_the_same_question(fake_llm):
    interview = make(fake_llm(follow_up("Why that option?")))
    interview.start()
    reply = interview.answer("We migrated the database.")
    assert reply == "Why that option?"
    assert len(interview.exchanges) == 1
    assert [t.speaker for t in interview.current.turns] == ["interviewer", "candidate", "interviewer"]


def test_move_on_asks_the_next_question(fake_llm):
    interview = make(fake_llm(move_on("Got it.")))
    interview.start()
    reply = interview.answer("A full answer.")
    assert reply.startswith("Got it.")
    assert interview.questions[1].prompt.strip() in reply
    assert len(interview.exchanges) == 2


def test_follow_up_limit_forces_a_move(fake_llm):
    llm = fake_llm(follow_up(), follow_up(), follow_up("One more?"))
    interview = make(llm, max_follow_ups=2)
    interview.start()
    interview.answer("a")
    interview.answer("b")
    reply = interview.answer("c")
    # The model wanted a third follow-up but the limit is two, so the round moves on.
    assert len(interview.exchanges) == 2
    assert interview.questions[1].prompt.strip() in reply
    assert "no follow-ups left" in llm.calls[-1]["prompt"]


def test_last_question_ends_the_interview(fake_llm):
    interview = make(fake_llm(move_on()), count=1)
    interview.start()
    reply = interview.answer("answer")
    assert interview.done
    assert "That's all the questions" in reply
    with pytest.raises(RuntimeError):
        interview.answer("too late")


def test_skip_moves_on_without_calling_the_model(fake_llm):
    llm = fake_llm()
    interview = make(llm)
    interview.start()
    interview.skip()
    assert interview.exchanges[0].skipped
    assert len(interview.exchanges) == 2
    assert llm.calls == []


def test_prompt_carries_the_question_and_transcript(fake_llm):
    llm = fake_llm(follow_up())
    interview = make(llm)
    interview.start()
    interview.answer("I led the migration to Postgres.")
    call = llm.calls[0]
    assert interview.questions[0].strong_signals[0] in call["system"]
    assert "Candidate: I led the migration to Postgres." in call["prompt"]
    assert "0 of 3 follow-ups" in call["prompt"]
