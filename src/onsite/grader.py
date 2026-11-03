"""Grades a finished round against the rubric.

The model must quote the candidate's own words as evidence for every score. Each quote is then checked
against the transcript, and a score with no real quote behind it gets no credit. This stops the grader from
rewarding things the candidate never said.
"""

import re
from difflib import SequenceMatcher
from functools import cache
from statistics import mean
from typing import Literal

from pydantic import BaseModel, Field, create_model

from onsite import prompts
from onsite.llm import LLM
from onsite.models import Criterion, Exchange, Level, Track, TrackBank

Score = Literal[1, 2, 3, 4]

MIN_QUOTE_CHARS = 8
FUZZY_MATCH = 0.85


class CriterionGrade(BaseModel):
    evidence: list[str] = Field(
        description="Exact quotes of the candidate's own words that support the score. Copy them word for word."
    )
    reasoning: str = Field(description="Why this score, judged against the anchors and the level bar.")
    score: Score


class ScoredCriterion(BaseModel):
    criterion_id: str
    name: str
    score: Score
    reasoning: str
    evidence: list[str]
    rejected_quotes: list[str] = Field(default_factory=list)
    capped: bool = False


class GradedQuestion(BaseModel):
    question_id: str
    title: str
    based_on: str | None = None
    skipped: bool
    scores: list[ScoredCriterion]
    strengths: list[str]
    gaps: list[str]
    stronger_answer: str

    @property
    def mean(self) -> float:
        return mean(s.score for s in self.scores)


class RoundReport(BaseModel):
    track: Track
    level: Level
    questions: list[GradedQuestion]
    criterion_means: dict[str, float]
    mean: float
    recommendation: str


@cache
def grade_schema(track: Track, rubric_ids: tuple[str, ...]) -> type[BaseModel]:
    # One required field per rubric item, so the model cannot skip or invent a criterion.
    scores = create_model(f"{track.value.title()}Scores", **{cid: (CriterionGrade, ...) for cid in rubric_ids})
    return create_model(
        "QuestionGrade",
        scores=(scores, ...),
        strengths=(list[str], Field(description="What the candidate did well, specific to this answer.")),
        gaps=(list[str], Field(description="What was missing or weak, most important first.")),
        stronger_answer=(
            str,
            Field(description="In a few sentences, what a strong answer at this level would have included."),
        ),
    )


def grade_round(llm: LLM, track_bank: TrackBank, level: Level, exchanges: list[Exchange]) -> RoundReport:
    graded = [grade_exchange(llm, track_bank, level, ex) for ex in exchanges]
    criterion_means = {
        c.id: mean(next(s.score for s in g.scores if s.criterion_id == c.id) for g in graded)
        for c in track_bank.rubric
    }
    overall = mean(criterion_means.values())
    return RoundReport(
        track=track_bank.track,
        level=level,
        questions=graded,
        criterion_means=criterion_means,
        mean=overall,
        recommendation=recommend(overall, criterion_means),
    )


def grade_exchange(llm: LLM, track_bank: TrackBank, level: Level, exchange: Exchange) -> GradedQuestion:
    q = exchange.question
    if exchange.skipped or not any(t.speaker == "candidate" for t in exchange.turns):
        return GradedQuestion(
            question_id=q.id,
            title=q.title,
            based_on=q.based_on,
            skipped=True,
            scores=[_no_evidence(c, "Question was skipped.") for c in track_bank.rubric],
            strengths=[],
            gaps=["Question was skipped."],
            stronger_answer="",
        )

    schema = grade_schema(track_bank.track, tuple(c.id for c in track_bank.rubric))
    result = llm.structured(
        system=prompts.grader_system(track_bank, level, q),
        prompt=prompts.grader_prompt(exchange),
        schema=schema,
        effort="high",
    )

    said = "\n".join(t.text for t in exchange.turns if t.speaker == "candidate")
    scores = [_verify(c, getattr(result.scores, c.id), said) for c in track_bank.rubric]
    return GradedQuestion(
        question_id=q.id,
        title=q.title,
        based_on=q.based_on,
        skipped=False,
        scores=scores,
        strengths=result.strengths,
        gaps=result.gaps,
        stronger_answer=result.stronger_answer,
    )


HIRE = ("Strong Hire", "Hire", "Lean Hire")
NO_HIRE = ("No Hire", "Strong No Hire")


def band(score: float) -> str:
    """The decision an average score points to, before any red flags."""
    if score >= 3.5:
        return "Strong Hire"
    if score >= 3.0:
        return "Hire"
    if score >= 2.75:
        return "Lean Hire"
    if score >= 2.25:
        return "Lean No Hire"
    if score >= 1.75:
        return "No Hire"
    return "Strong No Hire"


def recommend(overall: float, criterion_means: dict[str, float]) -> str:
    decision = band(overall)
    # A clear red flag on any one rubric item blocks a hire, the way a hiring committee would.
    red_flag = min(criterion_means.values()) <= 1.5
    if red_flag and decision in HIRE:
        decision = "Lean No Hire"
    return decision


def quote_found(quote: str, text: str) -> bool:
    """True if the quote really appears in the text, allowing small differences in punctuation or wording."""
    q, t = _normalize(quote), _normalize(text)
    if len(q) < MIN_QUOTE_CHARS:
        return False
    if q in t:
        return True

    q_words, t_words = q.split(), t.split()
    n = len(q_words)
    for i in range(max(1, len(t_words) - n + 1)):
        window = " ".join(t_words[i : i + n])
        if SequenceMatcher(None, q, window, autojunk=False).ratio() >= FUZZY_MATCH:
            return True
    return False


def _verify(criterion: Criterion, grade: CriterionGrade, said: str) -> ScoredCriterion:
    real = [e for e in grade.evidence if quote_found(e, said)]
    fake = [e for e in grade.evidence if e not in real]
    capped = not real and grade.score > 1
    return ScoredCriterion(
        criterion_id=criterion.id,
        name=criterion.name,
        score=1 if capped else grade.score,
        reasoning=grade.reasoning,
        evidence=real,
        rejected_quotes=fake,
        capped=capped,
    )


def _no_evidence(criterion: Criterion, reason: str) -> ScoredCriterion:
    return ScoredCriterion(criterion_id=criterion.id, name=criterion.name, score=1, reasoning=reason, evidence=[])


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())
