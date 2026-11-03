from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Track(StrEnum):
    BEHAVIORAL = "behavioral"
    CODING = "coding"
    SYSTEM_DESIGN = "system_design"


class Level(StrEnum):
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"


class Criterion(BaseModel):
    """One thing the interviewer scores, with a description of each score from 1 to 4."""

    id: str
    name: str
    description: str
    anchors: dict[int, str]

    @field_validator("anchors")
    @classmethod
    def needs_every_score(cls, anchors: dict[int, str]) -> dict[int, str]:
        if sorted(anchors) != [1, 2, 3, 4]:
            raise ValueError("anchors must describe scores 1, 2, 3 and 4")
        return anchors


class Question(BaseModel):
    id: str
    track: Track
    title: str
    prompt: str
    strong_signals: list[str] = Field(min_length=1)
    follow_ups: list[str] = Field(default_factory=list)
    levels: list[Level] = Field(default_factory=lambda: list(Level))
    based_on: str | None = Field(default=None, description="The line from the candidate's resume it asks about.")


class TrackBank(BaseModel):
    """Everything needed to run one kind of interview: the rubric, the bar per level and the questions."""

    track: Track
    rubric: list[Criterion] = Field(min_length=1)
    level_bar: dict[Level, str]
    questions: list[Question] = Field(min_length=1)

    def question(self, question_id: str) -> Question:
        for q in self.questions:
            if q.id == question_id:
                return q
        raise KeyError(f"no {self.track} question with id {question_id!r}")


class Turn(BaseModel):
    speaker: Literal["interviewer", "candidate"]
    text: str


class Exchange(BaseModel):
    """One question and everything said about it."""

    question: Question
    turns: list[Turn] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    skipped: bool = False

    def transcript(self) -> str:
        return "\n\n".join(f"{t.speaker.capitalize()}: {t.text}" for t in self.turns)
