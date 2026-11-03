"""Reads your resume so the interviewer can ask about the work you've really done."""

from pathlib import Path

# Plenty for a few pages. Anything longer is cut, so a huge file can't blow up every prompt.
MAX_CHARS = 20_000
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


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
