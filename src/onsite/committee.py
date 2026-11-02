"""The hiring committee: reads the feedback from every round and makes one decision for the whole loop.

The decision comes from fixed rules, so the same scores always give the same result. The model writes the
committee's notes: what the rounds agree on, the biggest concern, the level call and what to practise next.
Its claims about patterns across rounds are checked against the rounds that really happened.
"""

from statistics import mean
from typing import Literal

from pydantic import BaseModel, Field

from onsite import bank, prompts
from onsite.grader import HIRE, NO_HIRE, RoundReport, band
from onsite.llm import LLM
from onsite.models import Level

MAX_FOCUS = 3


class Theme(BaseModel):
    pattern: str = Field(description="Something the candidate did, good or bad, in more than one round.")
    rounds: list[int] = Field(description="The number of every round where it showed up.")


class CommitteeNotes(BaseModel):
    summary: str = Field(description="Two or three sentences a hiring manager could read on their own.")
    themes: list[Theme] = Field(description="Patterns seen in more than one round. Empty if there are none.")
    strongest_signal: str = Field(description="The clearest evidence in the candidate's favour, naming the round.")
    biggest_concern: str = Field(description="What would most likely sink them in a real loop, naming the round.")
    level_call: Literal["at_level", "one_level_down", "not_ready"] = Field(
        description="at_level: meets the bar for the level they applied for. one_level_down: misses that bar "
        "but clearly meets the bar one level below. not_ready: misses both."
    )
    focus: list[str] = Field(description="Up to three specific things to practise, most important first.")


class LoopReport(BaseModel):
    level: Level
    planned: int
    finished: int = Field(description="Rounds the candidate saw through to the end.")
    rounds: list[RoundReport]
    mean: float
    decision: str
    blocked_by: list[int] = Field(description="Rounds whose No Hire stopped the loop from being a hire.")
    hire_level: Level | None
    summary: str
    themes: list[Theme]
    strongest_signal: str
    biggest_concern: str
    focus: list[str]

    @property
    def complete(self) -> bool:
        return self.finished == self.planned


def decide(rounds: list[RoundReport]) -> tuple[str, list[int]]:
    """The loop's decision and the rounds that blocked a hire, if any.

    Every round counts the same. One interviewer saying No Hire blocks a hire, like in a real committee.
    """
    decision = band(mean(r.mean for r in rounds))
    blockers = [i for i, r in enumerate(rounds, 1) if r.recommendation in NO_HIRE]
    if blockers and decision in HIRE:
        return "Lean No Hire", blockers
    return decision, []


def review(
    llm: LLM, level: Level, rounds: list[RoundReport], planned: int | None = None, finished: int | None = None
) -> LoopReport:
    """Make the committee's decision for a loop, from the graded rounds in the order they happened.

    `planned` is how many rounds the loop was meant to have, and `finished` how many the candidate saw through.
    The last graded round may be one they stopped part way through.
    """
    if not rounds:
        raise ValueError("the committee needs at least one graded round")
    planned = planned or len(rounds)
    finished = len(rounds) if finished is None else finished
    decision, blocked_by = decide(rounds)
    below = one_below(level)

    tracks = list(dict.fromkeys(r.track for r in rounds))
    notes = llm.structured(
        system=prompts.committee_system(level, below, [bank.load(t) for t in tracks]),
        prompt=prompts.committee_prompt(rounds, planned, finished, decision, blocked_by),
        schema=CommitteeNotes,
        effort="high",
    )

    if decision in HIRE:
        hire_level = level
    elif notes.level_call == "one_level_down" and below:
        hire_level = below
    else:
        hire_level = None

    return LoopReport(
        level=level,
        planned=planned,
        finished=finished,
        rounds=rounds,
        mean=mean(r.mean for r in rounds),
        decision=decision,
        blocked_by=blocked_by,
        hire_level=hire_level,
        summary=notes.summary,
        themes=real_themes(notes.themes, len(rounds)),
        strongest_signal=notes.strongest_signal,
        biggest_concern=notes.biggest_concern,
        focus=notes.focus[:MAX_FOCUS],
    )


def real_themes(themes: list[Theme], round_count: int) -> list[Theme]:
    """Keep only themes that name at least two rounds, all of which really happened."""
    kept = []
    for theme in themes:
        rounds = sorted(set(theme.rounds))
        if len(rounds) >= 2 and all(1 <= r <= round_count for r in rounds):
            kept.append(Theme(pattern=theme.pattern, rounds=rounds))
    return kept


def one_below(level: Level) -> Level | None:
    levels = list(Level)
    i = levels.index(level)
    return levels[i - 1] if i else None
