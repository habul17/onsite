"""Voice mode: hear the interviewer and answer out loud. Everything runs on your machine.

Needs the `voice` extra: `uv sync --extra voice`.
"""

from collections.abc import Callable

from onsite.voice.listen import Transcriber
from onsite.voice.speak import Speaker


class Voice:
    """Speaks the interviewer's lines and turns your spoken answers into text."""

    def __init__(self, speaker: Speaker | None = None, transcriber: Transcriber | None = None):
        self.speaker = speaker or Speaker()
        self.transcriber = transcriber or Transcriber()

    def load(self) -> None:
        """Load both models, downloading them the first time. Raises ImportError without the voice extra."""
        from onsite.voice import audio  # noqa: F401

        self.speaker.load()
        self.transcriber.load()

    def say(self, text: str) -> None:
        self.speaker.say(text)

    def record(self, wait: Callable[[], object]):
        from onsite.voice.audio import record

        return record(wait)

    def transcribe(self, samples, context: str = "") -> str:
        return self.transcriber.transcribe(samples, context)
