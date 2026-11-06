"""Runs one interview round: asks each question, follows up, and decides when to move on."""

from typing import Literal

from pydantic import BaseModel, Field

from onsite import prompts
from onsite.llm import LLM
from onsite.models import Exchange, Level, Question, TrackBank, Turn


class InterviewerTurn(BaseModel):
    action: Literal["continue", "move_on"] = Field(
        description="continue: say something and wait for the candidate on this same question. "
        "move_on: close this question."
    )
    message: str = Field(description="What you say out loud to the candidate.")
    note: str = Field(
        description="Private note for the hiring committee: what evidence you are still missing, "
        "or why you are moving on. Mention any hint you gave."
    )


class Interview:
    def __init__(
        self,
        llm: LLM,
        track_bank: TrackBank,
        level: Level,
        questions: list[Question],
        max_follow_ups: int = 3,
    ):
        if not questions:
            raise ValueError("an interview needs at least one question")
        self.llm = llm
        self.track_bank = track_bank
        self.level = level
        self.questions = questions
        self.max_follow_ups = max_follow_ups
        self.exchanges: list[Exchange] = []
        self.done = False

    @property
    def current(self) -> Exchange:
        return self.exchanges[-1]

    def start(self) -> str:
        """Open the round and ask the first question."""
        if self.exchanges:
            raise RuntimeError("interview already started")
        opening = (
            "Hi, thanks for making the time. I'll ask you a few questions and might dig into the details "
            "as we go. Take your time and think out loud. Let's get started."
        )
        return opening + "\n\n" + self._ask(self.questions[0])

    def answer(self, text: str) -> str:
        """Record what the candidate said and return what the interviewer says next."""
        self._check_running()
        self.current.turns.append(Turn(speaker="candidate", text=text.strip()))

        follow_ups_used = self._follow_ups_used()
        turn = self.llm.structured(
            system=prompts.interviewer_system(self.track_bank, self.level, self.current.question),
            prompt=prompts.interviewer_turn_prompt(
                self.current.transcript(), follow_ups_used, self.max_follow_ups
            ),
            schema=InterviewerTurn,
            effort="medium",
        )
        self.current.notes.append(turn.note)

        if turn.action == "continue" and follow_ups_used < self.max_follow_ups:
            self.current.turns.append(Turn(speaker="interviewer", text=turn.message))
            return turn.message
        return self._next(turn.message)

    def skip(self) -> str:
        """The candidate asked to skip the current question."""
        self._check_running()
        self.current.skipped = True
        self.current.notes.append("Candidate chose to skip this question.")
        return self._next("No problem, let's move on.")

    def answered(self) -> list[Exchange]:
        """The questions worth grading: ones the candidate answered or chose to skip."""
        return [ex for ex in self.exchanges if ex.skipped or any(t.speaker == "candidate" for t in ex.turns)]

    def _ask(self, question: Question) -> str:
        exchange = Exchange(question=question)
        exchange.turns.append(Turn(speaker="interviewer", text=question.prompt.strip()))
        self.exchanges.append(exchange)
        return question.prompt.strip()

    def _next(self, closing_line: str) -> str:
        self.current.turns.append(Turn(speaker="interviewer", text=closing_line))
        if len(self.exchanges) < len(self.questions):
            return closing_line + "\n\n" + self._ask(self.questions[len(self.exchanges)])
        self.done = True
        return closing_line + "\n\nThat's all the questions I have. Thanks for your time today."

    def _follow_ups_used(self) -> int:
        # The first interviewer turn is the question itself; every later one is a follow-up.
        return sum(t.speaker == "interviewer" for t in self.current.turns) - 1

    def _check_running(self) -> None:
        if not self.exchanges:
            raise RuntimeError("call start() first")
        if self.done:
            raise RuntimeError("interview is over")
