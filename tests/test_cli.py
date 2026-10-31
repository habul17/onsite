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
