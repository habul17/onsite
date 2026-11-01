from typer.testing import CliRunner

from onsite import bank, cli
from onsite.grader import CriterionGrade, grade_schema
from onsite.interviewer import InterviewerTurn
from onsite.models import Track

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
    assert list(tmp_path.glob("*.json"))


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
