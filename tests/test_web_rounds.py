import json

import pytest

from onsite.interviewer import InterviewerTurn
from onsite.models import Level, Track
from onsite.web.rounds import NothingToGrade, RoundBusy, RoundNotFound, RoundOver, Rounds, with_board, with_code
from onsite.whiteboard import Arrow, Board, Box
from test_cli import ANSWER, MOVE_ON, track_grade

PROBE = InterviewerTurn(action="continue", message="Why a heap?", note="no complexity yet")
CODE = "def solve(nums):\n    return sorted(nums)\n"


def coding_round(fake, tmp_path):
    rounds = Rounds(fake, tmp_path / "reports")
    r, opening = rounds.start(Track.CODING, Level.SENIOR, seed=1)
    return rounds, r, opening


def test_start_opens_with_the_first_question(tmp_path, fake_llm):
    rounds, r, opening = coding_round(fake_llm(), tmp_path)

    assert r.interview.questions[0].prompt.strip() in opening
    assert (r.question_number, r.question_count) == (1, 1)
    assert rounds.get(r.id) is r


def test_code_is_sent_only_when_it_changes(tmp_path, fake_llm):
    fake = fake_llm(PROBE, PROBE, PROBE)
    rounds, r, _ = coding_round(fake, tmp_path)

    assert rounds.answer(r.id, "Sort it first.", code=CODE) == "Why a heap?"
    rounds.answer(r.id, "Sorting is n log n.", code=CODE)
    rounds.answer(r.id, "", code=CODE + "# done\n")

    said = [t.text for t in r.interview.current.turns if t.speaker == "candidate"]
    assert said[0] == f"Sort it first.\n\nMy code so far:\n```\n{CODE.rstrip()}\n```"
    assert said[1] == "Sorting is n log n."
    assert said[2].startswith("My code so far:") and "# done" in said[2]


def test_an_empty_answer_never_reaches_the_model(tmp_path, fake_llm):
    fake = fake_llm(PROBE)
    rounds, r, _ = coding_round(fake, tmp_path)
    rounds.answer(r.id, "Sort it.", code=CODE)

    with pytest.raises(ValueError):
        rounds.answer(r.id, "   ", code=CODE)
    assert len(fake.calls) == 1


def test_finish_grades_and_saves_once(tmp_path, fake_llm):
    fake = fake_llm(MOVE_ON, track_grade(Track.CODING, 3))
    rounds, r, _ = coding_round(fake, tmp_path)
    rounds.answer(r.id, ANSWER)

    done = rounds.finish(r.id)
    again = rounds.finish(r.id)

    assert done.report.mean == 3 and again.report is done.report
    assert len(fake.calls) == 2  # one interviewer turn, one grade, nothing for the second finish
    saved = json.loads(done.saved_to.with_suffix(".json").read_text())
    assert saved["track"] == "coding" and saved["taken_at"]
    assert ANSWER in done.saved_to.read_text()


def test_finishing_early_grades_what_was_answered(tmp_path, fake_llm):
    fake = fake_llm(PROBE, track_grade(Track.CODING, 2))
    rounds, r, _ = coding_round(fake, tmp_path)
    rounds.answer(r.id, ANSWER)

    assert not r.interview.done
    assert rounds.finish(r.id).report.mean == 2
    with pytest.raises(RoundOver):
        rounds.answer(r.id, "One more thing.")


def test_nothing_to_grade(tmp_path, fake_llm):
    rounds, r, _ = coding_round(fake_llm(), tmp_path)
    with pytest.raises(NothingToGrade):
        rounds.finish(r.id)
    assert not (tmp_path / "reports").exists()


def test_no_answers_once_the_questions_run_out(tmp_path, fake_llm):
    rounds, r, _ = coding_round(fake_llm(MOVE_ON), tmp_path)
    assert "That's all the questions I have" in rounds.answer(r.id, ANSWER)
    with pytest.raises(RoundOver):
        rounds.answer(r.id, "Wait, one more thing.")
    with pytest.raises(RoundOver):
        rounds.skip(r.id)


def test_a_second_click_while_thinking_is_refused(tmp_path, fake_llm):
    rounds, r, _ = coding_round(fake_llm(), tmp_path)
    with r.lock:
        with pytest.raises(RoundBusy):
            rounds.answer(r.id, ANSWER)
    assert not r.lock.locked()


def test_skip_moves_on_without_the_model(tmp_path, fake_llm):
    fake = fake_llm()
    rounds = Rounds(fake, tmp_path)
    r, _ = rounds.start(Track.BEHAVIORAL, Level.MID, seed=1)

    reply = rounds.skip(r.id)
    assert r.interview.questions[1].prompt.strip() in reply
    assert r.question_number == 2 and not fake.calls


def test_unknown_round():
    with pytest.raises(RoundNotFound):
        Rounds(None, None).get("nope")


@pytest.mark.parametrize(
    ("text", "code", "seen", "sent"),
    [
        ("Hi", None, "", "Hi"),
        ("Hi", "  \n", "", "Hi"),
        ("Hi", "x = 1", "x = 1", "Hi"),
        ("", "x = 1", "", "My code so far:\n```\nx = 1\n```"),
    ],
)
def test_with_code(text, code, seen, sent):
    assert with_code(text, code, seen) == sent


BOARD = Board(
    boxes=[
        Box(id="a", kind="service", label="API", x=0, y=0),
        Box(id="d", kind="database", label="Postgres", x=200, y=0),
    ],
    arrows=[Arrow(source="a", target="d", label="writes")],
)


def test_the_whiteboard_is_sent_only_when_it_changes(tmp_path, fake_llm):
    rounds = Rounds(fake_llm(PROBE, PROBE, PROBE), tmp_path)
    r, _ = rounds.start(Track.SYSTEM_DESIGN, Level.SENIOR, seed=1)

    rounds.answer(r.id, "Writes go to Postgres.", board=BOARD)
    rounds.answer(r.id, "Reads too.", board=BOARD)
    moved = BOARD.model_copy(deep=True)
    moved.boxes[1].x = 900  # moving a box changes nothing the interviewer reads
    rounds.answer(r.id, "Same picture.", board=moved)

    said = [t.text for t in r.interview.current.turns if t.speaker == "candidate"]
    assert said[0] == (
        "Writes go to Postgres.\n\nMy whiteboard so far:\nComponents:\n- API (service)\n- Postgres (database)\n"
        "Connections:\n- API -> Postgres: writes"
    )
    assert said[1:] == ["Reads too.", "Same picture."]


def test_a_drawing_alone_is_an_answer(tmp_path, fake_llm):
    rounds = Rounds(fake_llm(PROBE), tmp_path)
    r, _ = rounds.start(Track.SYSTEM_DESIGN, Level.SENIOR, seed=1)
    rounds.answer(r.id, "", board=BOARD)
    with pytest.raises(ValueError):
        rounds.answer(r.id, " ", board=BOARD)
    with pytest.raises(ValueError):
        rounds.answer(r.id, "", board=Board())


def test_with_board():
    assert with_board("Hi", None, "") == "Hi"
    assert with_board("Hi", "", "") == "Hi"
    assert with_board("Hi", "x", "x") == "Hi"
    assert with_board("", "x", "") == "My whiteboard so far:\nx"
