"""Turns a graded round into a Markdown report you can keep and compare over time."""

from datetime import datetime

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
