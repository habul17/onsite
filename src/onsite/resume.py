"""Reads your resume so the interviewer can ask about the work you've really done."""

from pathlib import Path

from pydantic import BaseModel, Field

from onsite import prompts
from onsite.grader import quote_found
from onsite.llm import LLM
from onsite.models import Level, Question, Track, TrackBank

# Plenty for a few pages. Anything longer is cut, so a huge file can't blow up every prompt.
MAX_CHARS = 20_000
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}

# Coding rounds use problems from the bank. Your resume doesn't change what a fair coding problem is.
TRACKS = (Track.BEHAVIORAL, Track.SYSTEM_DESIGN)


class ResumeError(ValueError):
    pass


def read_resume(path: Path) -> str:
    """The resume's text from a PDF, Markdown or plain text file."""
    if not path.is_file():
        raise ResumeError(f"No file at {path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(path)
    elif suffix in TEXT_SUFFIXES:
        text = path.read_text(errors="replace")
    else:
        raise ResumeError(f"Can't read {suffix or 'files without an extension'}. Use a PDF, .md or .txt file.")

    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    if not text:
        hint = " It may be a scanned image. Try exporting it again as text." if suffix == ".pdf" else ""
        raise ResumeError(f"Found no text in {path.name}.{hint}")
    return text[:MAX_CHARS]


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as e:
        raise ResumeError(f"Couldn't read {path.name}: {e}") from e


class ResumeQuestion(BaseModel):
    resume_quote: str = Field(description="The words on the resume this question is about, copied exactly.")
    title: str
    prompt: str = Field(description="What the interviewer says out loud.")
    strong_signals: list[str] = Field(description="What a strong answer at this level covers.")
    follow_ups: list[str]


class ResumeQuestions(BaseModel):
    questions: list[ResumeQuestion]


def questions_from_resume(llm: LLM, track_bank: TrackBank, level: Level, resume: str, count: int) -> list[Question]:
    """Up to `count` questions about the resume. Any question that doesn't point at a real line is dropped."""
    if track_bank.track not in TRACKS:
        raise ValueError(f"{track_bank.track} rounds don't use the resume")
    if count < 1:
        return []

    result = llm.structured(
        system=prompts.resume_questions_system(track_bank, level, count),
        prompt=prompts.resume_questions_prompt(resume),
        schema=ResumeQuestions,
        effort="high",
    )
    kept: list[Question] = []
    used: set[str] = set()
    for q in result.questions:
        key = " ".join(q.resume_quote.lower().split())
        # Two questions about the same line would ask for the same story twice.
        if key in used or not q.strong_signals or not quote_found(q.resume_quote, resume):
            continue
        used.add(key)
        kept.append(
            Question(
                id=f"resume-{len(kept) + 1}",
                track=track_bank.track,
                title=q.title,
                prompt=q.prompt,
                strong_signals=q.strong_signals,
                follow_ups=q.follow_ups,
                levels=[level],
                based_on=q.resume_quote.strip(),
            )
        )
    return kept[:count]
