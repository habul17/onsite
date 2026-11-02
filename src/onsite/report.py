"""Turns graded rounds and loops into Markdown reports you can keep and compare over time."""

from datetime import datetime

from onsite.committee import LoopReport
from onsite.grader import RoundReport
from onsite.models import Exchange
from onsite.prompts import TRACK_NAMES


def to_markdown(report: RoundReport, exchanges: list[Exchange], when: datetime | None = None) -> str:
    when = when or datetime.now()
    track = TRACK_NAMES[report.track]
    lines = [
        f"# {track.capitalize()} round, {report.level.value} level",
        "",
        f"{when:%Y-%m-%d %H:%M}",
        "",
        f"**Recommendation: {report.recommendation}** (average {report.mean:.2f} / 4)",
        "",
        "| Rubric item | Average |",
        "| --- | --- |",
    ]
    names = {s.criterion_id: s.name for s in report.questions[0].scores}
    lines += [f"| {names[cid]} | {avg:.2f} |" for cid, avg in report.criterion_means.items()]

    for graded, exchange in zip(report.questions, exchanges, strict=True):
        lines += ["", f"## {graded.title}", ""]
        if graded.skipped:
            lines += ["Skipped.", ""]
            continue

        lines += ["| Rubric item | Score | Why |", "| --- | --- | --- |"]
        for s in graded.scores:
            why = s.reasoning.replace("\n", " ").replace("|", "\\|")
            if s.capped:
                why += " *(No real quote supported a higher score, so it was set to 1.)*"
            lines.append(f"| {s.name} | {s.score} | {why} |")

        lines += ["", "**Strengths**", ""] + [f"- {x}" for x in graded.strengths]
        lines += ["", "**Gaps**", ""] + [f"- {x}" for x in graded.gaps]
        lines += ["", "**What a strong answer adds**", "", graded.stronger_answer]

        evidence = [(s.name, e) for s in graded.scores for e in s.evidence]
        if evidence:
            lines += ["", "**Evidence used**", ""] + [f'- {name}: "{e}"' for name, e in evidence]

        lines += ["", "<details><summary>Transcript</summary>", "", exchange.transcript(), "", "</details>"]

    return "\n".join(lines) + "\n"


def loop_to_markdown(loop: LoopReport, round_files: list[str] | None = None, when: datetime | None = None) -> str:
    """The committee's write-up for a whole loop, with links to each round's own report."""
    when = when or datetime.now()
    lines = [f"# Onsite loop, {loop.level.value} level", "", f"{when:%Y-%m-%d %H:%M}", ""]
    lines.append(f"**Decision: {loop.decision}** (average {loop.mean:.2f} / 4)")
    if loop.hire_level and loop.hire_level != loop.level:
        lines += ["", f"The committee would hire at **{loop.hire_level.value}** level instead."]
    if not loop.complete:
        done = f"{loop.finished} of {loop.planned}"
        lines += ["", f"*Unfinished loop: {done} rounds finished. Treat the decision as a rough guide.*"]
    lines += ["", loop.summary, "", "| Round | Interviewer's recommendation | Average |", "| --- | --- | --- |"]

    for i, r in enumerate(loop.rounds, 1):
        name = f"{i}. {TRACK_NAMES[r.track].capitalize()}"
        if round_files:
            name = f"[{name}]({round_files[i - 1]})"
        blocked = " (blocked a hire)" if i in loop.blocked_by else ""
        lines.append(f"| {name} | {r.recommendation}{blocked} | {r.mean:.2f} |")

    lines += ["", f"**Strongest signal:** {loop.strongest_signal}", "", f"**Biggest concern:** {loop.biggest_concern}"]
    if loop.themes:
        lines += ["", "## Patterns across rounds", ""]
        lines += [f"- {t.pattern} (rounds {', '.join(map(str, t.rounds))})" for t in loop.themes]
    if loop.focus:
        lines += ["", "## What to practise next", ""]
        lines += [f"{n}. {item}" for n, item in enumerate(loop.focus, 1)]
    return "\n".join(lines) + "\n"
