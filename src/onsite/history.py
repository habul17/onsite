"""Reads every report you've saved, so you can see how you're doing over time."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from onsite.committee import LoopReport
from onsite.grader import RoundReport

# Reports are saved as 2026-11-04-0930-coding.json, or inside a loop folder named 2026-11-04-0930-loop.
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{4})")


@dataclass(frozen=True)
class Session:
    """One graded round, on its own or as part of a loop."""

    when: datetime
    report: RoundReport
    path: Path
    in_loop: bool


@dataclass(frozen=True)
class Loop:
    when: datetime
    report: LoopReport
    path: Path


@dataclass
class History:
    sessions: list[Session] = field(default_factory=list)
    loops: list[Loop] = field(default_factory=list)
    unreadable: list[Path] = field(default_factory=list)


def load(folder: Path) -> History:
    """Every round and loop saved in the folder, oldest first.

    Each round counts once. Rounds from a loop are read from their own files, not again from loop.json, so a
    loop whose committee never met still counts.
    """
    history = History()
    if not folder.is_dir():
        return history

    for path in sorted(folder.rglob("*.json")):
        try:
            text = path.read_text()
            if path.name == "loop.json":
                loop = LoopReport.model_validate_json(text)
                history.loops.append(Loop(when=when(loop.taken_at, path), report=loop, path=path))
            else:
                report = RoundReport.model_validate_json(text)
                in_loop = path.parent != folder
                history.sessions.append(Session(when(report.taken_at, path), report, path, in_loop))
        except (OSError, UnicodeDecodeError, ValidationError):
            history.unreadable.append(path)

    history.sessions.sort(key=lambda s: s.when)
    history.loops.sort(key=lambda lp: lp.when)
    return history


def when(taken_at: datetime | None, path: Path) -> datetime:
    """When a report was made: from the report itself, then its file or folder name, then the file's age."""
    if taken_at:
        return taken_at
    for name in (path.stem, path.parent.name):
        if match := STAMP.match(name):
            return datetime.strptime(match.group(1), "%Y-%m-%d-%H%M")
    return datetime.fromtimestamp(path.stat().st_mtime)
