"""The design-round whiteboard: boxes and arrows, written out as text the interviewer and grader can read.

Text, not a picture, so what you drew becomes part of the transcript. The grader can quote it like
anything you said, and the quote check works on it the same way.

Only kinds, labels and arrows reach the text, in the order you added them. Where a box sits doesn't, so
tidying up the board never counts as a new drawing.
"""

from typing import Literal

from pydantic import BaseModel, Field

MAX_BOXES = 60
MAX_ARROWS = 120
MAX_LABEL = 80

Kind = Literal["client", "service", "database", "cache", "queue", "storage", "note"]

KIND_NAMES: dict[str, str] = {
    "client": "Client",
    "service": "Service",
    "database": "Database",
    "cache": "Cache",
    "queue": "Queue",
    "storage": "Storage",
    "note": "Note",
}


class Box(BaseModel):
    id: str = Field(max_length=40)
    kind: Kind = "service"
    label: str = Field(default="", max_length=MAX_LABEL)
    # Kept so a board can be drawn again. They never reach the text.
    x: float = 0
    y: float = 0


class Arrow(BaseModel):
    source: str = Field(max_length=40)
    target: str = Field(max_length=40)
    label: str = Field(default="", max_length=MAX_LABEL)


class Board(BaseModel):
    boxes: list[Box] = Field(default_factory=list, max_length=MAX_BOXES)
    arrows: list[Arrow] = Field(default_factory=list, max_length=MAX_ARROWS)


def describe(board: Board) -> str:
    """The board as plain text, or "" when nothing is drawn. Same board in, same text out."""
    names = _names(board.boxes)
    parts = [b for b in board.boxes if b.kind != "note"]
    notes = [b for b in board.boxes if b.kind == "note" and b.label.strip()]

    lines = []
    if parts:
        lines.append("Components:")
        lines += [f"- {names[b.id]} ({KIND_NAMES[b.kind].lower()})" for b in parts]

    # An arrow must join two different components that are really on the board. Notes aren't components.
    joinable = {b.id for b in parts}
    arrows: list[str] = []
    for a in board.arrows:
        if a.source == a.target or a.source not in joinable or a.target not in joinable:
            continue
        label = _clean(a.label)
        line = f"- {names[a.source]} -> {names[a.target]}" + (f": {label}" if label else "")
        if line not in arrows:
            arrows.append(line)
    if arrows:
        lines += ["Connections:", *arrows]

    if notes:
        lines += ["Notes:", *(f"- {_clean(b.label)}" for b in notes)]
    return "\n".join(lines)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _names(boxes: list[Box]) -> dict[str, str]:
    """A unique, readable name for every box. Unlabelled boxes are named by kind, and repeats get a number."""
    names: dict[str, str] = {}
    used: dict[str, int] = {}
    for b in boxes:
        base = _clean(b.label) or f"Unnamed {KIND_NAMES[b.kind].lower()}"
        used[base.lower()] = used.get(base.lower(), 0) + 1
        names[b.id] = base if used[base.lower()] == 1 else f"{base} ({used[base.lower()]})"
    return names
