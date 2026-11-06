"""Rounds running in the browser. Each one lives in memory until it is graded and saved.

This is the same interviewer and grader as the command line. Only the way you answer is different.
"""

import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from onsite import bank
from onsite.grader import RoundReport, grade_round
from onsite.interviewer import Interview
from onsite.llm import LLM
from onsite.models import Level, Track
from onsite.report import save_round


class RoundNotFound(KeyError):
    pass


class RoundBusy(RuntimeError):
    """The interviewer is still answering, so a second click must not send the same answer twice."""


class RoundOver(RuntimeError):
    pass


class NothingToGrade(RuntimeError):
    pass


@dataclass
class WebRound:
    id: str
    track: Track
    level: Level
    interview: Interview
    lock: threading.Lock = field(default_factory=threading.Lock)
    # The editor's text the interviewer has already seen, so unchanged code isn't sent with every answer.
    code_seen: str = ""
    report: RoundReport | None = None
    saved_to: Path | None = None

    @property
    def question_number(self) -> int:
        return len(self.interview.exchanges)

    @property
    def question_count(self) -> int:
        return len(self.interview.questions)


class Rounds:
    def __init__(self, llm: LLM, reports: Path):
        self.llm = llm
        self.reports = reports
        self._rounds: dict[str, WebRound] = {}

    def start(
        self, track: Track, level: Level, questions: int | None = None, seed: int | None = None
    ) -> tuple[WebRound, str]:
        """A new round and the interviewer's opening words."""
        picked = bank.pick(track, level, questions or bank.DEFAULT_QUESTIONS[track], seed=seed)
        interview = Interview(self.llm, bank.load(track), level, picked, bank.DEFAULT_FOLLOW_UPS[track])
        r = WebRound(id=secrets.token_urlsafe(12), track=track, level=level, interview=interview)
        self._rounds[r.id] = r
        return r, interview.start()

    def get(self, round_id: str) -> WebRound:
        try:
            return self._rounds[round_id]
        except KeyError:
            raise RoundNotFound(round_id) from None

    def answer(self, round_id: str, text: str, code: str | None = None) -> str:
        """Send what the candidate said, and the editor's code if it changed, and get the interviewer's reply."""
        r = self.get(round_id)
        with self._hold(r):
            message = with_code(text, code, r.code_seen)
            if not message:
                raise ValueError("say something or write some code first")
            reply = r.interview.answer(message)
            if code is not None:
                r.code_seen = code
            return reply

    def skip(self, round_id: str) -> str:
        r = self.get(round_id)
        with self._hold(r):
            return r.interview.skip()

    def finish(self, round_id: str) -> WebRound:
        """Grade what was answered so far and save the report. Finishing twice doesn't grade twice."""
        r = self.get(round_id)
        with self._hold(r, allow_over=True):
            if r.report is None:
                exchanges = r.interview.answered()
                if not exchanges:
                    raise NothingToGrade("answer at least one question before finishing")
                report = grade_round(self.llm, r.interview.track_bank, r.level, exchanges)
                report.taken_at = datetime.now()
                r.saved_to = save_round(report, exchanges, self.reports)
                r.report = report
                r.interview.done = True
            return r

    @contextmanager
    def _hold(self, r: WebRound, allow_over: bool = False) -> Iterator[None]:
        if not r.lock.acquire(blocking=False):
            raise RoundBusy("still working on your last answer")
        try:
            if r.report is not None and not allow_over:
                raise RoundOver("this round has been graded")
            if r.interview.done and not allow_over:
                raise RoundOver("the interviewer has no more questions. Finish to get graded")
            yield
        finally:
            r.lock.release()


def with_code(text: str, code: str | None, seen: str) -> str:
    """The answer as the interviewer reads it: the words, then the code if it changed since last time."""
    text = text.strip()
    if code is None or not code.strip() or code == seen:
        return text
    block = f"My code so far:\n```\n{code.rstrip()}\n```"
    return f"{text}\n\n{block}" if text else block
