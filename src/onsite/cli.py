"""Command-line mock interview: `onsite start`."""

from datetime import datetime
from pathlib import Path
from typing import Annotated

import anthropic
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from onsite import bank
from onsite.grader import RoundReport, grade_round
from onsite.interviewer import Interview
from onsite.llm import AnthropicLLM, LLMError
from onsite.models import Level, Track
from onsite.prompts import TRACK_NAMES
from onsite.report import to_markdown

app = typer.Typer(add_completion=False, help="An AI mock interviewer for software engineering onsites.")
console = Console()

# Coding and design rounds are a long back-and-forth, so they get many more turns per question.
DEFAULT_QUESTIONS = {Track.BEHAVIORAL: 3, Track.CODING: 1, Track.SYSTEM_DESIGN: 1}
DEFAULT_FOLLOW_UPS = {Track.BEHAVIORAL: 3, Track.CODING: 12, Track.SYSTEM_DESIGN: 15}

HELP_TEXT = (
    "Press Enter on an empty line to send your answer. "
    "Wrap code in ``` so blank lines inside it are kept.\n"
    "Type /skip to skip a question, /quit to stop and get graded on what you answered."
)


@app.command()
def start(
    track: Annotated[Track, typer.Option(help="Which round to practise.")] = Track.BEHAVIORAL,
    level: Annotated[Level, typer.Option(help="The level you are interviewing for.")] = Level.MID,
    questions: Annotated[int | None, typer.Option(help="How many questions to ask.")] = None,
    follow_ups: Annotated[int | None, typer.Option(help="Most follow-ups per question.")] = None,
    seed: Annotated[int | None, typer.Option(help="Pick the same questions every time.")] = None,
    out: Annotated[Path, typer.Option(help="Folder to save the report in.")] = Path("reports"),
):
    """Run a mock interview round, then get graded like a hiring committee."""
    try:
        llm = AnthropicLLM()
    except anthropic.AnthropicError as e:
        console.print(f"[red]Could not set up the model client:[/red] {e}")
        console.print("Set ANTHROPIC_API_KEY, or copy .env.example to .env and fill it in.")
        raise typer.Exit(1)

    track_bank = bank.load(track)
    picked = bank.pick(track, level, questions or DEFAULT_QUESTIONS[track], seed=seed)
    interview = Interview(llm, track_bank, level, picked, max_follow_ups=follow_ups or DEFAULT_FOLLOW_UPS[track])

    console.print(Panel(HELP_TEXT, title=f"{TRACK_NAMES[track].capitalize()} round, {level.value} level"))
    say(interview.start())

    try:
        while not interview.done:
            text = read_answer()
            if text == "/quit":
                break
            if text == "/skip":
                say(interview.skip())
                continue
            with console.status("Interviewer is thinking..."):
                reply = interview.answer(text)
            say(reply)
    except KeyboardInterrupt:
        console.print("\nStopping early.")
    except LLMError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    exchanges = [ex for ex in interview.exchanges if any(t.speaker == "candidate" for t in ex.turns) or ex.skipped]
    if not exchanges:
        console.print("Nothing to grade.")
        raise typer.Exit()

    try:
        with console.status("Hiring committee is grading..."):
            report = grade_round(llm, track_bank, level, exchanges)
    except LLMError as e:
        console.print(f"[red]Grading failed: {e}[/red]")
        raise typer.Exit(1)

    show(report)
    path = save(report, exchanges, out)
    console.print(f"\nFull report with evidence and transcript: [bold]{path}[/bold]")


@app.command("questions")
def list_questions(track: Annotated[Track, typer.Option()] = Track.BEHAVIORAL):
    """List the questions in the bank for a track."""
    table = Table("id", "title", "levels")
    for q in bank.load(track).questions:
        table.add_row(q.id, q.title, ", ".join(lv.value for lv in q.levels))
    console.print(table)


def say(message: str) -> None:
    console.print()
    console.print(Text("Interviewer", style="bold cyan"))
    console.print(Text(message))
    console.print()


def read_answer() -> str:
    lines: list[str] = []
    in_code = False
    while True:
        try:
            line = console.input("[bold green]> [/bold green]" if not lines else "  ")
        except EOFError:
            return "\n".join(lines) or "/quit"
        if line.strip().startswith("```"):
            in_code = not in_code
        if not line.strip() and not in_code:
            if lines:
                return "\n".join(lines)
            continue
        lines.append(line)


def show(report: RoundReport) -> None:
    table = Table(title="Scores", show_lines=False)
    table.add_column("Rubric item")
    for i, _ in enumerate(report.questions, 1):
        table.add_column(f"Q{i}", justify="center")
    table.add_column("Avg", justify="center", style="bold")

    for criterion in report.questions[0].scores:
        row = [criterion.name]
        for graded in report.questions:
            s = next(x for x in graded.scores if x.criterion_id == criterion.criterion_id)
            row.append(f"{s.score}*" if s.capped else str(s.score))
        row.append(f"{report.criterion_means[criterion.criterion_id]:.1f}")
        table.add_row(*row)

    console.print()
    console.print(table)
    if any(s.capped for g in report.questions for s in g.scores):
        console.print("* no real quote from your answers supported a higher score")

    colour = "green" if "Hire" in report.recommendation and "No" not in report.recommendation else "red"
    console.print(f"\nRecommendation: [bold {colour}]{report.recommendation}[/bold {colour}] ({report.mean:.2f} / 4)")

    for i, graded in enumerate(report.questions, 1):
        if graded.skipped:
            continue
        console.print(f"\n[bold]Q{i}. {graded.title}[/bold]")
        for gap in graded.gaps[:3]:
            console.print(Text(f"  - {gap}"))


def save(report: RoundReport, exchanges, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stem = folder / f"{now:%Y-%m-%d-%H%M}-{report.track.value}"
    stem.with_suffix(".md").write_text(to_markdown(report, exchanges, now))
    stem.with_suffix(".json").write_text(report.model_dump_json(indent=2))
    return stem.with_suffix(".md")
