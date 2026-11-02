"""Command-line mock interviews: one round with `onsite start`, a full loop with `onsite loop`."""

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated

import anthropic
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from onsite import bank, committee
from onsite.committee import LoopReport
from onsite.grader import HIRE, RoundReport, grade_round
from onsite.interviewer import Interview
from onsite.llm import AnthropicLLM, LLMError
from onsite.models import Exchange, Level, Question, Track
from onsite.prompts import TRACK_NAMES
from onsite.report import loop_to_markdown, to_markdown

app = typer.Typer(add_completion=False, help="An AI mock interviewer for software engineering onsites.")
console = Console()

# Coding and design rounds are a long back-and-forth, so they get many more turns per question.
DEFAULT_QUESTIONS = {Track.BEHAVIORAL: 3, Track.CODING: 1, Track.SYSTEM_DESIGN: 1}
DEFAULT_FOLLOW_UPS = {Track.BEHAVIORAL: 3, Track.CODING: 12, Track.SYSTEM_DESIGN: 15}

# The usual shape of a real onsite: problem solving first, then design, then the behavioral chat.
DEFAULT_LOOP = [Track.CODING, Track.SYSTEM_DESIGN, Track.BEHAVIORAL]

HELP_TEXT = (
    "Press Enter on an empty line to send your answer. "
    "Wrap code in ``` so blank lines inside it are kept.\n"
    "Type /skip to skip a question, /quit to stop and get graded on what you answered."
)

MUTED = "[yellow]Your microphone sounds muted or very quiet. Check which input device is selected.[/yellow]"

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
    llm = make_llm()
    speech = load_voice() if voice else None

    track_bank = bank.load(track)
    picked = bank.pick(track, level, questions or DEFAULT_QUESTIONS[track], seed=seed)
    interview = Interview(llm, track_bank, level, picked, max_follow_ups=follow_ups or DEFAULT_FOLLOW_UPS[track])

    title = f"{TRACK_NAMES[track].capitalize()} round, {level.value} level"
    console.print(Panel(VOICE_HELP_TEXT if speech else HELP_TEXT, title=title))

    run_round(interview, speech)
    exchanges = answered(interview)
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


@app.command()
def loop(
    level: Annotated[Level, typer.Option(help="The level you are interviewing for.")] = Level.MID,
    rounds: Annotated[
        list[Track] | None,
        typer.Option(
            "--round", help="Add a round. Repeat it to build your own loop. Default: coding, system design, behavioral."
        ),
    ] = None,
    seed: Annotated[int | None, typer.Option(help="Pick the same questions every time.")] = None,
    out: Annotated[Path, typer.Option(help="Folder to save the reports in.")] = Path("reports"),
    voice: Annotated[bool, typer.Option("--voice", help="Hear the interviewers and answer out loud.")] = False,
):
    """Run a full onsite loop, round after round, then get one hiring committee decision."""
    plan = rounds or DEFAULT_LOOP
    try:
        questions = pick_loop(plan, level, seed)
    except ValueError as e:
        console.print(f"[red]Can't build that loop:[/red] {e}")
        raise typer.Exit(1)

    llm = make_llm()
    speech = load_voice() if voice else None
    names = ", ".join(TRACK_NAMES[t] for t in plan)
    intro = (
        f"{len(plan)} rounds: {names}. Each round has its own interviewer, and you get no scores until the "
        "committee decides at the end, like a real loop.\n" + (VOICE_HELP_TEXT if speech else HELP_TEXT)
    )
    console.print(Panel(intro, title=f"Onsite loop, {level.value} level"))

    now = datetime.now()
    folder = out / f"{now:%Y-%m-%d-%H%M}-loop"
    graded: list[RoundReport] = []
    files: list[str] = []
    finished = 0
    for i, (track, picked) in enumerate(zip(plan, questions, strict=True), 1):
        console.print()
        console.rule(f"Round {i} of {len(plan)}: {TRACK_NAMES[track]}")
        track_bank = bank.load(track)
        interview = Interview(llm, track_bank, level, picked, max_follow_ups=DEFAULT_FOLLOW_UPS[track])
        done = run_round(interview, speech)
        if done:
            finished += 1

        if exchanges := answered(interview):
            try:
                with console.status("Interviewer is writing up their feedback..."):
                    report = grade_round(llm, track_bank, level, exchanges)
            except LLMError as e:
                console.print(f"[red]Grading failed: {e}[/red]")
                raise typer.Exit(1)
            # Saved straight away, so a problem later on never loses a round you already did.
            graded.append(report)
            files.append(write_round(report, exchanges, folder / f"{i}-{track.value}", now).name)

        if not done:
            break
        if i < len(plan) and not take_break():
            break

    if not graded:
        console.print("Nothing to grade.")
        raise typer.Exit()

    try:
        with console.status("Hiring committee is meeting..."):
            result = committee.review(llm, level, graded, planned=len(plan), finished=finished)
    except LLMError as e:
        console.print(f"[red]The committee could not meet: {e}[/red]")
        console.print(f"Your round reports are saved in [bold]{folder}[/bold]")
        raise typer.Exit(1)

    show_loop(result)
    (folder / "loop.md").write_text(loop_to_markdown(result, files, now))
    (folder / "loop.json").write_text(result.model_dump_json(indent=2))
    console.print(f"\nCommittee write-up and every round's report: [bold]{folder / 'loop.md'}[/bold]")


@app.command("voice-check")
def voice_check():
    """Check your speakers and microphone before a voice round."""
    speech = load_voice()
    say("This is how I'll sound in the interview. Now say a sentence, and press Enter when you're done.", speech)
    console.print("[bold red]Recording.[/bold red] Press Enter when you're done.")
    samples = speech.record(lambda: console.input(""))
    if speech.is_quiet(samples):
        console.print(MUTED)
        raise typer.Exit(1)
    with console.status("Transcribing..."):
        text = speech.transcribe(samples)
    if not text:
        console.print("I didn't catch any words. Try speaking a bit closer to the microphone.")
        raise typer.Exit(1)
    console.print(Text.assemble("I heard: ", (text, "green")))
    console.print("If that's right, you're ready: onsite start --voice")


@app.command("questions")
def list_questions(track: Annotated[Track, typer.Option()] = Track.BEHAVIORAL):
    """List the questions in the bank for a track."""
    table = Table("id", "title", "levels")
    for q in bank.load(track).questions:
        table.add_row(q.id, q.title, ", ".join(lv.value for lv in q.levels))
    console.print(table)


def run_round(interview: Interview, speech=None) -> bool:
    """Run the round until it ends. Returns False if the candidate stopped early."""
    try:
        say(interview.start(), speech)
        while not interview.done:
            # What the candidate is answering: the question or the latest follow-up.
            text = read_answer(speech, context=interview.current.turns[-1].text)
            if text == "/quit":
                return False
            if text == "/skip":
                say(interview.skip(), speech)
                continue
            with console.status("Interviewer is thinking..."):
                reply = interview.answer(text)
            say(reply, speech)
    except (KeyboardInterrupt, EOFError):
        console.print("\nStopping early.")
        return False
    except LLMError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    return True


def answered(interview: Interview) -> list[Exchange]:
    """The questions worth grading: ones the candidate answered or chose to skip."""
    return [ex for ex in interview.exchanges if any(t.speaker == "candidate" for t in ex.turns) or ex.skipped]


def make_llm() -> AnthropicLLM:
    try:
        return AnthropicLLM()
    except anthropic.AnthropicError as e:
        console.print(f"[red]Could not set up the model client:[/red] {e}")
        console.print("Set ANTHROPIC_API_KEY and try again.")
        raise typer.Exit(1)


def pick_loop(plan: list[Track], level: Level, seed: int | None) -> list[list[Question]]:
    """Questions for every round. Two rounds of the same kind never share a question."""
    pools = {
        track: bank.pick(track, level, count * DEFAULT_QUESTIONS[track], seed=seed)
        for track, count in Counter(plan).items()
    }
    picked = []
    for track in plan:
        n = DEFAULT_QUESTIONS[track]
        picked.append(pools[track][:n])
        pools[track] = pools[track][n:]
    return picked


def take_break() -> bool:
    """Pause between rounds. Returns False if the candidate wants to stop."""
    try:
        reply = console.input("\nThat round is over. Take a break, then press Enter for the next one, or type /quit. ")
    except (KeyboardInterrupt, EOFError):
        return False
    return reply.strip() != "/quit"


def load_voice():
    try:
        from onsite.voice import Voice, audio  # noqa: F401  (audio fails to import without the extra)

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
            if speech.is_quiet(samples):
                console.print(MUTED)
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


def show_loop(result: LoopReport) -> None:
    table = Table(title="Your loop")
    table.add_column("Round")
    table.add_column("Interviewer's call")
    table.add_column("Avg", justify="center", style="bold")
    for i, r in enumerate(result.rounds, 1):
        call = r.recommendation + (" (blocked a hire)" if i in result.blocked_by else "")
        table.add_row(f"{i}. {TRACK_NAMES[r.track].capitalize()}", call, f"{r.mean:.1f}")
    console.print()
    console.print(table)

    colour = "green" if result.decision in HIRE else "red"
    console.print(f"\nCommittee decision: [bold {colour}]{result.decision}[/bold {colour}] ({result.mean:.2f} / 4)")
    if result.hire_level and result.hire_level != result.level:
        console.print(f"The committee would hire you at [bold]{result.hire_level.value}[/bold] level instead.")
    if not result.complete:
        done = f"{result.finished} of {result.planned}"
        console.print(f"[yellow]You finished {done} rounds, so take this as a rough guide.[/yellow]")
    console.print()
    console.print(Text(result.summary))
    console.print(Text.assemble(("\nStrongest signal: ", "bold"), result.strongest_signal))
    console.print(Text.assemble(("Biggest concern: ", "bold"), result.biggest_concern))
    if result.focus:
        console.print("\n[bold]What to practise next[/bold]")
        for n, item in enumerate(result.focus, 1):
            console.print(Text(f"  {n}. {item}"))


def save(report: RoundReport, exchanges, folder: Path) -> Path:
    now = datetime.now()
    return write_round(report, exchanges, folder / f"{now:%Y-%m-%d-%H%M}-{report.track.value}", now)


def write_round(report: RoundReport, exchanges, stem: Path, when: datetime) -> Path:
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".md").write_text(to_markdown(report, exchanges, when))
    stem.with_suffix(".json").write_text(report.model_dump_json(indent=2))
    return stem.with_suffix(".md")
