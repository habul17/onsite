from datetime import datetime

import pytest
from typer.testing import CliRunner

from onsite import bank, cli
from onsite.committee import CommitteeNotes, LoopReport
from onsite.grader import CriterionGrade, RoundReport, grade_schema
from onsite.interviewer import InterviewerTurn
from onsite.models import Track
from onsite.resume import ResumeQuestion, ResumeQuestions
from test_committee import round_report

ANSWER = "I owned the rollout and wrote the migration plan myself. It cut costs by 30 percent."


def grade(score):
    tb = bank.load(Track.BEHAVIORAL)
    schema = grade_schema(tb.track, tuple(c.id for c in tb.rubric))
    g = CriterionGrade(evidence=["wrote the migration plan myself"], reasoning="clear", score=score)
    return schema(scores={c.id: g for c in tb.rubric}, strengths=["owned it"], gaps=["no lesson"], stronger_answer="Add a lesson.")


def run(monkeypatch, tmp_path, fake, stdin):
    monkeypatch.setattr(cli, "AnthropicLLM", lambda: fake)
    args = ["start", "--track", "behavioral", "--level", "senior", "--questions", "1", "--seed", "1", "--out", str(tmp_path)]
    return CliRunner().invoke(cli.app, args, input=stdin)


def test_full_round_prints_scores_and_saves_a_report(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(InterviewerTurn(action="move_on", message="Okay.", note="done"), grade(3))
    result = run(monkeypatch, tmp_path, fake, ANSWER + "\n\n")

    assert result.exit_code == 0, result.output
    assert "Recommendation" in result.output
    assert "Hire" in result.output
    reports = list(tmp_path.glob("*.md"))
    assert len(reports) == 1
    text = reports[0].read_text()
    assert ANSWER in text
    assert '"wrote the migration plan myself"' in text
    saved = RoundReport.model_validate_json(next(tmp_path.glob("*.json")).read_text())
    assert saved.taken_at is not None
    assert reports[0].name.startswith(f"{saved.taken_at:%Y-%m-%d-%H%M}-behavioral")


def test_quit_before_answering_grades_nothing(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm()
    result = run(monkeypatch, tmp_path, fake, "/quit\n")
    assert result.exit_code == 0
    assert "Nothing to grade" in result.output
    assert fake.calls == []


def test_code_blocks_keep_blank_lines(monkeypatch):
    lines = iter(["```python", "def f():", "", "    return 1", "```", ""])
    monkeypatch.setattr(cli.console, "input", lambda prompt="": next(lines))
    assert cli.read_answer() == "```python\ndef f():\n\n    return 1\n```"


class FakeVoice:
    def __init__(self, *heard):
        self.heard = list(heard)
        self.said = []
        self.contexts = []

    def say(self, text):
        self.said.append(text)

    def record(self, wait):
        wait()  # the candidate presses Enter to stop
        return "samples"

    def is_quiet(self, samples):
        return samples == "silence"

    def transcribe(self, samples, context=""):
        self.contexts.append(context)
        return self.heard.pop(0)


def run_voice(monkeypatch, tmp_path, fake, voice, stdin):
    monkeypatch.setattr(cli, "load_voice", lambda: voice)
    monkeypatch.setattr(cli, "AnthropicLLM", lambda: fake)
    args = ["start", "--track", "behavioral", "--questions", "1", "--seed", "1", "--out", str(tmp_path), "--voice"]
    return CliRunner().invoke(cli.app, args, input=stdin)


def test_voice_round_speaks_and_grades_the_spoken_answer(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(InterviewerTurn(action="move_on", message="Okay.", note="done"), grade(3))
    voice = FakeVoice(ANSWER)
    # Enter to start talking, Enter to stop, Enter to send.
    result = run_voice(monkeypatch, tmp_path, fake, voice, "\n\n\n")

    assert result.exit_code == 0, result.output
    assert voice.said[0].startswith("Hi, thanks for making the time.")
    assert voice.said[-1].endswith("Thanks for your time today.")
    question = bank.pick(Track.BEHAVIORAL, cli.Level.MID, 1, seed=1)[0]
    assert voice.contexts == [question.prompt.strip()]
    assert ANSWER in fake.calls[0]["prompt"]
    assert ANSWER in next(tmp_path.glob("*.md")).read_text()


def test_voice_answer_can_be_recorded_again(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(InterviewerTurn(action="move_on", message="Okay.", note="done"), grade(3))
    voice = FakeVoice("um I forgot what I", ANSWER)
    result = run_voice(monkeypatch, tmp_path, fake, voice, "\n\nr\n\n\n")

    assert result.exit_code == 0, result.output
    assert ANSWER in fake.calls[0]["prompt"]
    assert "um I forgot" not in fake.calls[0]["prompt"]


def test_typing_still_works_in_voice_mode(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(InterviewerTurn(action="move_on", message="Okay.", note="done"), grade(3))
    voice = FakeVoice()
    result = run_voice(monkeypatch, tmp_path, fake, voice, ANSWER + "\n\n")

    assert result.exit_code == 0, result.output
    assert voice.contexts == []
    assert ANSWER in fake.calls[0]["prompt"]


def test_voice_without_the_extra_explains_how_to_install_it(monkeypatch, tmp_path):
    from onsite import voice

    def missing(self):
        raise ImportError("No module named 'miniaudio'")

    monkeypatch.setattr(voice.Voice, "load", missing)
    monkeypatch.setattr(cli, "AnthropicLLM", lambda: object())
    result = CliRunner().invoke(cli.app, ["start", "--voice", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "uv sync --extra voice" in result.output


def test_voice_check_plays_a_line_and_shows_what_it_heard(monkeypatch):
    voice = FakeVoice("Testing one two three.")
    monkeypatch.setattr(cli, "load_voice", lambda: voice)
    result = CliRunner().invoke(cli.app, ["voice-check"], input="\n")

    assert result.exit_code == 0, result.output
    assert voice.said and "how I'll sound" in voice.said[0]
    assert "I heard: Testing one two three." in result.output


def test_voice_check_warns_about_a_muted_mic(monkeypatch):
    voice = FakeVoice()
    voice.record = lambda wait: (wait(), "silence")[1]
    monkeypatch.setattr(cli, "load_voice", lambda: voice)
    result = CliRunner().invoke(cli.app, ["voice-check"], input="\n")

    assert result.exit_code == 1
    assert "muted" in result.output


def track_grade(track, score):
    tb = bank.load(track)
    schema = grade_schema(tb.track, tuple(c.id for c in tb.rubric))
    g = CriterionGrade(evidence=["wrote the migration plan myself"], reasoning="clear", score=score)
    return schema(scores={c.id: g for c in tb.rubric}, strengths=["clear"], gaps=["thin"], stronger_answer="More.")


def committee_notes():
    return CommitteeNotes(
        summary="Consistent loop.",
        themes=[],
        strongest_signal="Clean code in round 1.",
        biggest_concern="Shallow deep dive in round 2.",
        level_call="at_level",
        focus=["Estimate scale before drawing boxes"],
    )


MOVE_ON = InterviewerTurn(action="move_on", message="Okay.", note="done")
LOOP_ARGS = ["loop", "--level", "senior", "--round", "coding", "--round", "system_design", "--seed", "1"]


def run_loop(monkeypatch, tmp_path, fake, stdin, args=LOOP_ARGS):
    monkeypatch.setattr(cli, "AnthropicLLM", lambda: fake)
    return CliRunner().invoke(cli.app, [*args, "--out", str(tmp_path)], input=stdin)


def test_loop_runs_every_round_then_the_committee_decides(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(
        MOVE_ON, track_grade(Track.CODING, 3), MOVE_ON, track_grade(Track.SYSTEM_DESIGN, 3), committee_notes()
    )
    # Answer round 1, Enter after the break, answer round 2.
    result = run_loop(monkeypatch, tmp_path, fake, f"{ANSWER}\n\n\n{ANSWER}\n\n")

    assert result.exit_code == 0, result.output
    assert "Round 1 of 2: coding" in result.output and "Round 2 of 2: system design" in result.output
    assert "Committee decision: Hire" in result.output
    assert "Estimate scale before drawing boxes" in result.output
    assert "The committee's decision: Hire." in fake.calls[-1]["prompt"]

    folder = next(tmp_path.iterdir())
    assert folder.name.endswith("-loop")
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["1-coding.json", "1-coding.md", "2-system_design.json", "2-system_design.md", "loop.json", "loop.md"]
    assert "[1. Coding](1-coding.md)" in (folder / "loop.md").read_text()
    saved = LoopReport.model_validate_json((folder / "loop.json").read_text())
    assert saved.taken_at is not None and all(r.taken_at for r in saved.rounds)
    assert saved.rounds[0].taken_at <= saved.rounds[1].taken_at
    assert ANSWER in (folder / "2-system_design.md").read_text()


def test_quitting_part_way_grades_what_was_done(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(MOVE_ON, track_grade(Track.CODING, 3), committee_notes())
    result = run_loop(monkeypatch, tmp_path, fake, f"{ANSWER}\n\n\n/quit\n")

    assert result.exit_code == 0, result.output
    assert "finished 1 of 2" in result.output
    assert "The candidate finished 1 of 2 rounds." in fake.calls[-1]["prompt"]
    assert (next(tmp_path.iterdir()) / "1-coding.md").exists()


def test_stopping_at_the_break_ends_the_loop(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(MOVE_ON, track_grade(Track.CODING, 3), committee_notes())
    result = run_loop(monkeypatch, tmp_path, fake, f"{ANSWER}\n\n/quit\n")

    assert result.exit_code == 0, result.output
    assert "Round 2 of 2" not in result.output
    assert "The candidate finished 1 of 2 rounds." in fake.calls[-1]["prompt"]


def test_quitting_before_any_answer_grades_nothing(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm()
    result = run_loop(monkeypatch, tmp_path, fake, "/quit\n")
    assert result.exit_code == 0
    assert "Nothing to grade" in result.output
    assert fake.calls == []


def test_round_reports_survive_if_the_committee_fails(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(MOVE_ON, track_grade(Track.CODING, 3))

    def fail(*args, **kwargs):
        raise cli.LLMError("Rate limited by the API.")

    monkeypatch.setattr(cli.committee, "review", fail)
    result = run_loop(monkeypatch, tmp_path, fake, f"{ANSWER}\n\n/quit\n")
    assert result.exit_code == 1
    assert "round reports are saved" in result.output
    assert (next(tmp_path.iterdir()) / "1-coding.md").exists()


def test_two_rounds_of_the_same_kind_get_different_questions():
    first, second = cli.pick_loop([Track.CODING, Track.CODING], cli.Level.MID, seed=3)
    assert len(first) == len(second) == 1
    assert first[0].id != second[0].id


def test_a_loop_bigger_than_the_bank_is_refused(monkeypatch, tmp_path, fake_llm):
    args = ["loop", *["--round", "system_design"] * 9]
    result = run_loop(monkeypatch, tmp_path, fake_llm(), "", args)
    assert result.exit_code == 1
    assert "Can't build that loop" in result.output


RESUME = """Jane Doe
Led the migration of the ledger from MySQL to Postgres with zero downtime
Built a fraud scoring service"""


def from_resume(*quotes):
    return ResumeQuestions(
        questions=[
            ResumeQuestion(
                resume_quote=q,
                title=f"About: {q}",
                prompt=f"Your resume says '{q}'. Walk me through it.",
                strong_signals=["specific"],
                follow_ups=[],
            )
            for q in quotes
        ]
    )


def resume_file(tmp_path, text=RESUME):
    path = tmp_path / "resume.md"
    path.write_text(text)
    return str(path)


def test_start_asks_about_the_resume(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(from_resume("Led the migration of the ledger"), MOVE_ON, grade(3))
    monkeypatch.setattr(cli, "AnthropicLLM", lambda: fake)
    args = ["start", "--questions", "1", "--resume", resume_file(tmp_path), "--out", str(tmp_path / "reports")]
    result = CliRunner().invoke(cli.app, args, input=ANSWER + "\n\n")

    assert result.exit_code == 0, result.output
    assert "Your resume says 'Led the migration of the ledger'. Walk me through it." in result.output
    assert "<resume>" in fake.calls[0]["prompt"] and "Postgres" in fake.calls[0]["prompt"]
    assert "Only" not in result.output


def test_questions_the_resume_cant_back_up_come_from_the_bank(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(from_resume("Rewrote the search engine in Rust"), MOVE_ON, grade(3))
    monkeypatch.setattr(cli, "AnthropicLLM", lambda: fake)
    args = ["start", "--questions", "1", "--seed", "1", "--resume", resume_file(tmp_path)]
    result = CliRunner().invoke(cli.app, [*args, "--out", str(tmp_path / "reports")], input=ANSWER + "\n\n")

    assert result.exit_code == 0, result.output
    assert "Only 0 of 1 behavioral questions could be tied to your resume" in result.output
    banked = bank.pick(Track.BEHAVIORAL, cli.Level.MID, 1, seed=1)[0]
    # Long questions wrap on screen, so compare the words and not the line breaks.
    assert " ".join(banked.prompt.split()) in " ".join(result.output.split())


def test_coding_rounds_ignore_the_resume(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm()
    monkeypatch.setattr(cli, "AnthropicLLM", lambda: fake)
    args = ["start", "--track", "coding", "--resume", resume_file(tmp_path), "--out", str(tmp_path / "reports")]
    result = CliRunner().invoke(cli.app, args, input="/quit\n")

    assert result.exit_code == 0, result.output
    assert "resume isn't used here" in result.output
    assert fake.calls == []


def test_a_resume_that_cant_be_read_stops_before_the_round(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm()
    monkeypatch.setattr(cli, "AnthropicLLM", lambda: fake)
    result = CliRunner().invoke(cli.app, ["start", "--resume", str(tmp_path / "missing.pdf")])
    assert result.exit_code == 1
    assert "Can't use that resume" in result.output
    assert fake.calls == []


def test_loop_uses_the_resume_for_design_but_not_coding(monkeypatch, tmp_path, fake_llm):
    fake = fake_llm(
        from_resume("Built a fraud scoring service"),
        MOVE_ON, track_grade(Track.SYSTEM_DESIGN, 3), MOVE_ON, track_grade(Track.CODING, 3), committee_notes(),
    )
    args = ["loop", "--round", "system_design", "--round", "coding", "--resume", resume_file(tmp_path)]
    result = run_loop(monkeypatch, tmp_path / "reports", fake, f"{ANSWER}\n\n\n{ANSWER}\n\n", args)

    assert result.exit_code == 0, result.output
    assert "Write 1 question." in fake.calls[0]["system"]
    assert "Your resume says 'Built a fraud scoring service'." in result.output
    assert "Committee decision" in result.output


def saved_round(folder, track, score, day, level=cli.Level.SENIOR):
    report = round_report(track, score, level)
    report.taken_at = datetime(2026, 10, day, 9)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{report.taken_at:%Y-%m-%d-%H%M}-{track.value}.json").write_text(report.model_dump_json())
    return report


def progress(folder, *args):
    return CliRunner().invoke(cli.app, ["progress", "--reports", str(folder), *args], terminal_width=120)


def test_progress_shows_trends_and_what_to_work_on(tmp_path):
    for day, score in enumerate([2, 3, 3, 4], 1):
        saved_round(tmp_path, Track.CODING, score, day)
    weak = saved_round(tmp_path, Track.BEHAVIORAL, 3, 5)
    weak.criterion_means["impact"] = 1.0
    (tmp_path / "2026-10-05-0900-behavioral.json").write_text(weak.model_dump_json())

    result = progress(tmp_path)
    assert result.exit_code == 0, result.output
    assert "5 rounds since 01 Oct 2026" in result.output
    assert "Coding, senior level: 4 rounds" in result.output
    assert "Behavioral, senior level: 1 round" in result.output
    assert "▲ +1.0" in result.output
    assert "Last 3 decisions, oldest first: Hire, Hire, Strong Hire" in result.output
    assert "Ready" in result.output
    assert "1. Impact (behavioral, senior): 1.0 lately" in result.output


def test_progress_says_which_way_a_weak_area_is_moving(tmp_path):
    saved_round(tmp_path, Track.CODING, 1, 1)
    saved_round(tmp_path, Track.CODING, 2, 2)
    result = progress(tmp_path)
    assert "(coding, senior): 2.0 lately, up from 1.0" in result.output
    assert "Ready" not in result.output


def test_progress_can_show_one_kind_of_round(tmp_path):
    saved_round(tmp_path, Track.CODING, 3, 1)
    saved_round(tmp_path, Track.BEHAVIORAL, 3, 2)
    result = progress(tmp_path, "--track", "behavioral")
    assert "Behavioral, senior level" in result.output
    assert "Coding" not in result.output
    assert "1 round since" in result.output


def test_progress_with_nothing_saved_says_how_to_start(tmp_path):
    result = progress(tmp_path / "reports")
    assert result.exit_code == 0
    assert "No graded rounds" in result.output and "onsite start" in result.output


def test_progress_skips_files_that_are_not_reports(tmp_path):
    saved_round(tmp_path, Track.CODING, 3, 1)
    (tmp_path / "notes.json").write_text("{}")
    result = progress(tmp_path)
    assert result.exit_code == 0
    assert "Skipped files that aren't onsite reports: notes.json" in result.output


def test_sparkline_uses_a_fixed_scale_and_the_last_points():
    assert cli.sparkline([1, 4]).plain == "▁█"
    assert cli.sparkline([0.5, 2.5, 9]).plain == "▁▅█"
    assert cli.sparkline([3.0] * 20).plain == "▆" * cli.SPARK_POINTS


@pytest.mark.parametrize(
    "change, shown", [(None, "-"), (0.02, "= 0.0"), (0.7, "▲ +0.7"), (-0.34, "▼ -0.3")]
)
def test_change_never_relies_on_colour_alone(change, shown):
    assert cli.change_text(change).plain == shown
