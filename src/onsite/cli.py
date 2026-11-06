"""Command-line mock interviews: one round with `onsite start`, a full loop with `onsite loop`, and
`onsite progress` to see how you are doing over time."""

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

from onsite import bank, committee, history
from onsite.bank import DEFAULT_FOLLOW_UPS, DEFAULT_QUESTIONS
from onsite.committee import LoopReport
from onsite.grader import HIRE, RoundReport, grade_round
from onsite.interviewer import Interview
from onsite.llm import AnthropicLLM, LLMError
from onsite.models import Level, Question, Track
from onsite.prompts import TRACK_NAMES
from onsite.report import loop_to_markdown, save_round, write_round
from onsite.resume import TRACKS as RESUME_TRACKS
from onsite.resume import ResumeError, questions_from_resume, read_resume

app = typer.Typer(add_completion=False, help="An AI mock interviewer for software engineering onsites.")
console = Console()

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
    resume: Annotated[
        Path | None,
        typer.Option(help="Your resume as a PDF, .md or .txt file. Behavioral and design questions will be about it."),
    ] = None,
):
    """Run a mock interview round, then get graded like a hiring committee."""
    resume_text = load_resume(resume) if resume else None
    llm = make_llm()
    speech = load_voice() if voice else None

    track_bank = bank.load(track)
    count = questions or DEFAULT_QUESTIONS[track]
    from_resume = resume_questions(llm, {track: count}, level, resume_text) if resume_text else {}
    mine = from_resume.get(track, [])
    picked = mine + bank.pick(track, level, count - len(mine), seed=seed)
    interview = Interview(llm, track_bank, level, picked, max_follow_ups=follow_ups or DEFAULT_FOLLOW_UPS[track])

    title = f"{TRACK_NAMES[track].capitalize()} round, {level.value} level"
    console.print(Panel(VOICE_HELP_TEXT if speech else HELP_TEXT, title=title))

    run_round(interview, speech)
    exchanges = interview.answered()
    if not exchanges:
        console.print("Nothing to grade.")
        raise typer.Exit()

    try:
        with console.status("Hiring committee is grading..."):
            report = grade_round(llm, track_bank, level, exchanges)
    except LLMError as e:
        console.print(f"[red]Grading failed: {e}[/red]")
        raise typer.Exit(1)
    report.taken_at = datetime.now()

    show(report)
    path = save_round(report, exchanges, out)
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
    resume: Annotated[
        Path | None,
        typer.Option(help="Your resume as a PDF, .md or .txt file. Behavioral and design questions will be about it."),
    ] = None,
):
    """Run a full onsite loop, round after round, then get one hiring committee decision."""
    plan = rounds or DEFAULT_LOOP
    try:
        questions = pick_loop(plan, level, seed)
    except ValueError as e:
        console.print(f"[red]Can't build that loop:[/red] {e}")
        raise typer.Exit(1)

    resume_text = load_resume(resume) if resume else None
    llm = make_llm()
    if resume_text:
        needed = {track: n * DEFAULT_QUESTIONS[track] for track, n in Counter(plan).items()}
        questions = pick_loop(plan, level, seed, resume_questions(llm, needed, level, resume_text))
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

        if exchanges := interview.answered():
            try:
                with console.status("Interviewer is writing up their feedback..."):
                    report = grade_round(llm, track_bank, level, exchanges)
            except LLMError as e:
                console.print(f"[red]Grading failed: {e}[/red]")
                raise typer.Exit(1)
            report.taken_at = datetime.now()
            # Saved straight away, so a problem later on never loses a round you already did.
            graded.append(report)
            files.append(write_round(report, exchanges, folder / f"{i}-{track.value}").name)

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
    result.taken_at = now

    show_loop(result)
    (folder / "loop.md").write_text(loop_to_markdown(result, files, now))
    (folder / "loop.json").write_text(result.model_dump_json(indent=2))
    console.print(f"\nCommittee write-up and every round's report: [bold]{folder / 'loop.md'}[/bold]")


@app.command()
def progress(
    reports: Annotated[Path, typer.Option(help="Folder your reports are saved in.")] = Path("reports"),
    track: Annotated[Track | None, typer.Option(help="Only this kind of round.")] = None,
    level: Annotated[Level | None, typer.Option(help="Only this level.")] = None,
):
    """See how you're doing over time, from every report you've saved."""
    past = history.load(reports)
    if past.unreadable:
        names = ", ".join(str(p.relative_to(reports)) for p in past.unreadable[:3])
        more = f" and {len(past.unreadable) - 3} more" if len(past.unreadable) > 3 else ""
        console.print(f"[yellow]Skipped files that aren't onsite reports: {names}{more}[/yellow]")

    groups = history.summarize(past, track, level)
    if not groups:
        console.print(f"No graded rounds in {reports} yet. Run onsite start or onsite loop first.")
        raise typer.Exit()

    count = sum(len(g.sessions) for g in groups)
    first = min(g.sessions[0].when for g in groups)
    console.print(f"\n[bold]Your progress[/bold]: {count} round{'s' if count != 1 else ''} since {first:%d %b %Y}")
    console.print("[dim]Scores are 1 to 4, and 3 meets the bar for your level. Earlier and Recent compare your first "
                  f"and last rounds, up to {history.WINDOW} each.[/dim]")
    for g in groups:
        show_progress(g)

    loops = [lp for lp in past.loops if (level is None or lp.report.level == level)] if track is None else []
    if loops:
        table = Table(title="Full loops", title_justify="left")
        for name in ("Date", "Level", "Decision", "Rounds", "Avg"):
            table.add_column(name, justify="center" if name in ("Rounds", "Avg") else "left")
        for lp in loops:
            r = lp.report
            done = f"{r.finished} of {r.planned}"
            table.add_row(f"{lp.when:%d %b %Y}", r.level.value, r.decision, done, f"{r.mean:.1f}")
        console.print()
        console.print(table)

    picked = history.focus(groups)
    console.print("\n[bold]What to work on next[/bold]")
    if not picked:
        console.print("Every rubric item meets the bar in your recent rounds. Try the next level up.")
    for n, (g, t) in enumerate(picked, 1):
        where = f"{TRACK_NAMES[g.track]}, {g.level.value}"
        moved = ""
        if t.change is not None and abs(t.change) >= 0.05:
            moved = f", {'up' if t.change > 0 else 'down'} from {t.earlier:.1f}"
        console.print(Text.assemble(f"  {n}. ", (t.name, "bold"), f" ({where}): {t.recent:.1f} lately{moved}"))


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
def list_questions(
    track: Annotated[Track | None, typer.Option(help="Only this kind of round. Leave out for every kind.")] = None,
    level: Annotated[Level | None, typer.Option(help="Only questions asked at this level.")] = None,
):
    """List the questions in the bank."""
    for t in [track] if track else list(Track):
        picked = [q for q in bank.load(t).questions if level is None or level in q.levels]
        for_level = f" for {level}" if level else ""
        title = f"{TRACK_NAMES[t].capitalize()}: {len(picked)} question{'s' if len(picked) != 1 else ''}{for_level}"
        table = Table("id", "title", "levels", title=title, title_justify="left", title_style="bold")
        for q in picked:
            table.add_row(q.id, q.title, level_range(q.levels))
        console.print(table)
        console.print()


def level_range(levels: list[Level]) -> str:
    """Levels as "all", "mid to staff" or "junior", rather than every level spelled out."""
    order = list(Level)
    spots = sorted(order.index(lv) for lv in set(levels))
    if len(spots) == len(order):
        return "all"
    if len(spots) > 1 and spots == list(range(spots[0], spots[-1] + 1)):
        joiner = "and" if len(spots) == 2 else "to"
        return f"{order[spots[0]]} {joiner} {order[spots[-1]]}"
    return ", ".join(order[i] for i in spots)


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


def make_llm() -> AnthropicLLM:
    try:
        return AnthropicLLM()
    except anthropic.AnthropicError as e:
        console.print(f"[red]Could not set up the model client:[/red] {e}")
        console.print("Set ANTHROPIC_API_KEY and try again.")
        raise typer.Exit(1)


def pick_loop(
    plan: list[Track], level: Level, seed: int | None, from_resume: dict[Track, list[Question]] | None = None
) -> list[list[Question]]:
    """Questions for every round, resume questions first. Two rounds of the same kind never share a question."""
    pools = {}
    for track, count in Counter(plan).items():
        needed = count * DEFAULT_QUESTIONS[track]
        mine = (from_resume or {}).get(track, [])[:needed]
        pools[track] = mine + bank.pick(track, level, needed - len(mine), seed=seed)
    picked = []
    for track in plan:
        n = DEFAULT_QUESTIONS[track]
        picked.append(pools[track][:n])
        pools[track] = pools[track][n:]
    return picked


def load_resume(path: Path) -> str:
    try:
        return read_resume(path)
    except ResumeError as e:
        console.print(f"[red]Can't use that resume:[/red] {e}")
        raise typer.Exit(1)


def resume_questions(llm, needed: dict[Track, int], level: Level, resume_text: str) -> dict[Track, list[Question]]:
    """Questions about the resume for every round that uses it. The bank covers whatever is still missing."""
    if not any(track in RESUME_TRACKS for track in needed):
        console.print("[yellow]Coding rounds use the question bank, so your resume isn't used here.[/yellow]")
        return {}

    found = {}
    for track, count in needed.items():
        if track not in RESUME_TRACKS:
            continue
        try:
            with console.status(f"Reading your resume for the {TRACK_NAMES[track]} round..."):
                found[track] = questions_from_resume(llm, bank.load(track), level, resume_text, count)
        except LLMError as e:
            console.print(f"[red]Could not write questions from your resume: {e}[/red]")
            raise typer.Exit(1)
        if len(found[track]) < count:
            console.print(
                f"[yellow]Only {len(found[track])} of {count} {TRACK_NAMES[track]} questions could be tied to "
                "your resume. The rest come from the question bank.[/yellow]"
            )
    return found


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


SPARK = "▁▂▃▄▅▆▇█"
SPARK_POINTS = 12


def sparkline(scores: list[float]) -> Text:
    """Scores on a fixed 1 to 4 scale, so a small change never looks bigger than it is. Latest point stands out."""
    points = scores[-SPARK_POINTS:]
    chars = [SPARK[round((min(max(x, 1), 4) - 1) / 3 * (len(SPARK) - 1))] for x in points]
    return Text.assemble(("".join(chars[:-1]), "dim"), (chars[-1], "bold cyan"))


def change_text(change: float | None) -> Text:
    """Direction as an arrow and a sign, with colour on top, so it never relies on colour alone."""
    if change is None:
        return Text("-", style="dim")
    if abs(change) < 0.05:
        return Text("= 0.0", style="dim")
    if change > 0:
        return Text(f"▲ +{change:.1f}", style="green")
    return Text(f"▼ {change:.1f}", style="red")


def show_progress(g: "history.TrackProgress") -> None:
    rounds = len(g.sessions)
    table = Table(
        title=f"{TRACK_NAMES[g.track].capitalize()}, {g.level.value} level: {rounds} round{'s' if rounds != 1 else ''}",
        title_justify="left",
    )
    table.add_column("Rubric item")
    for name in ("Earlier", "Recent", "Change"):
        table.add_column(name, justify="right")
    table.add_column("History")

    def row(t: "history.Trend", style: str = "") -> None:
        earlier = "-" if t.earlier is None else f"{t.earlier:.1f}"
        table.add_row(Text(t.name, style=style), earlier, f"{t.recent:.1f}", change_text(t.change), sparkline(t.scores))

    row(g.overall, "bold")
    table.add_section()
    for t in g.criteria:
        row(t)

    console.print()
    console.print(table)
    last = g.sessions[-history.WINDOW :]
    calls = ", ".join(s.report.recommendation for s in last)
    console.print(f"Last {len(last)} decision{'s' if len(last) != 1 else ''}, oldest first: {calls}")
    if g.ready:
        console.print(f"[green]✓ Ready:[/green] you met the bar in each of your last {history.WINDOW} rounds.")
