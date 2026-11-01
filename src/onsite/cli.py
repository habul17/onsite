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

VOICE_HELP_TEXT = (
    "Press Enter to start talking, and Enter again when you're done. "
    "You can still type, for example to write code.\n"
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
    voice: Annotated[bool, typer.Option("--voice", help="Hear the interviewer and answer out loud.")] = False,
):
    """Run a mock interview round, then get graded like a hiring committee."""
    try:
        llm = AnthropicLLM()
    except anthropic.AnthropicError as e:
        console.print(f"[red]Could not set up the model client:[/red] {e}")
        console.print("Set ANTHROPIC_API_KEY and try again.")
        raise typer.Exit(1)

    speech = load_voice() if voice else None

    track_bank = bank.load(track)
    picked = bank.pick(track, level, questions or DEFAULT_QUESTIONS[track], seed=seed)
    interview = Interview(llm, track_bank, level, picked, max_follow_ups=follow_ups or DEFAULT_FOLLOW_UPS[track])

    title = f"{TRACK_NAMES[track].capitalize()} round, {level.value} level"
    console.print(Panel(VOICE_HELP_TEXT if speech else HELP_TEXT, title=title))

    try:
        say(interview.start(), speech)
        while not interview.done:
            # What the candidate is answering: the question or the latest follow-up.
            text = read_answer(speech, context=interview.current.turns[-1].text)
            if text == "/quit":
                break
            if text == "/skip":
                say(interview.skip(), speech)
                continue
            with console.status("Interviewer is thinking..."):
                reply = interview.answer(text)
            say(reply, speech)
    except (KeyboardInterrupt, EOFError):
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


def load_voice():
    try:
        from onsite.voice import Voice

        speech = Voice()
        console.print("Loading the voice models. The first run downloads about 270 MB.")
        speech.load()
    except ImportError:
        console.print("[red]Voice mode needs a few extra packages.[/red] Run: uv sync --extra voice")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Could not start voice mode:[/red] {e}")
        raise typer.Exit(1)
    return speech


def say(message: str, speech=None) -> None:
    console.print()
    console.print(Text("Interviewer", style="bold cyan"))
    console.print(Text(message))
    console.print()
    if speech:
        try:
            speech.say(message)
        except Exception as e:  # a lost headset shouldn't end the round
            console.print(f"[red]Could not play audio:[/red] {e}")


def listen(speech, context: str) -> str:
    """Record until Enter, then show what was heard. Returns "" to go back to the prompt."""
    while True:
        console.print("[bold red]Recording.[/bold red] Press Enter when you're done.")
        try:
            samples = speech.record(lambda: console.input(""))
        except Exception as e:  # no microphone, or it's in use
            console.print(f"[red]Recording failed:[/red] {e}. You can type your answer instead.")
            return ""
        with console.status("Transcribing..."):
            text = speech.transcribe(samples, context)
        if not text:
            console.print("I didn't catch that. Press Enter to try again, or type your answer.")
            return ""
        console.print(Text(text, style="green"))
        if console.input("[dim]Press Enter to send it, or type r to record again.[/dim] ").strip().lower() != "r":
            return text


def read_answer(speech=None, context: str = "") -> str:
    lines: list[str] = []
    in_code = False
    while True:
        try:
            line = console.input("[bold green]> [/bold green]" if not lines else "  ")
        except EOFError:
            return "\n".join(lines) or "/quit"
        if speech and not lines and not line.strip():
            if spoken := listen(speech, context):
                return spoken
            continue
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
