"""Reads every report you've saved, so you can see how you're doing over time."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean

from pydantic import ValidationError

from onsite.committee import LoopReport
from onsite.grader import HIRE, RoundReport
from onsite.models import Level, Track

# Reports are saved as 2026-11-04-0930-coding.json, or inside a loop folder named 2026-11-04-0930-loop.
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{4})")

# Compare your last few rounds with your first few, so one good or bad day doesn't swing the trend.
WINDOW = 3
BAR = 3.0


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


@dataclass(frozen=True)
class Trend:
    """One score over time, oldest first."""

    name: str
    scores: list[float]

    @property
    def window(self) -> int:
        # Never let "earlier" and "recent" share a round, or a change would hide inside the overlap.
        return max(1, min(WINDOW, len(self.scores) // 2))

    @property
    def recent(self) -> float:
        return mean(self.scores[-self.window :])

    @property
    def earlier(self) -> float | None:
        return mean(self.scores[: self.window]) if len(self.scores) >= 2 else None

    @property
    def change(self) -> float | None:
        return None if self.earlier is None else self.recent - self.earlier


@dataclass(frozen=True)
class TrackProgress:
    """Every round of one kind at one level. Scores are judged against the level, so levels aren't mixed."""

    track: Track
    level: Level
    sessions: list[Session]
    overall: Trend
    criteria: list[Trend]

    @property
    def ready(self) -> bool:
        """Met the bar in each of the last few rounds."""
        last = self.sessions[-WINDOW:]
        return len(last) == WINDOW and all(s.report.recommendation in HIRE for s in last)


def summarize(history: History, track: Track | None = None, level: Level | None = None) -> list[TrackProgress]:
    groups: dict[tuple[Track, Level], list[Session]] = {}
    for s in history.sessions:
        if (track is None or s.report.track == track) and (level is None or s.report.level == level):
            groups.setdefault((s.report.track, s.report.level), []).append(s)

    order = {t: i for i, t in enumerate(Track)} | {lv: i for i, lv in enumerate(Level)}
    result = []
    for (t, lv), sessions in sorted(groups.items(), key=lambda kv: (order[kv[0][0]], order[kv[0][1]])):
        # Use the latest rubric. A rubric item an older report doesn't have just has fewer points.
        latest = sessions[-1].report
        names = {sc.criterion_id: sc.name for sc in latest.questions[0].scores}
        criteria = []
        for cid in latest.criterion_means:
            scores = [s.report.criterion_means[cid] for s in sessions if cid in s.report.criterion_means]
            criteria.append(Trend(names.get(cid, cid), scores))
        overall = Trend("Overall", [s.report.mean for s in sessions])
        result.append(TrackProgress(t, lv, sessions, overall, criteria))
    return result


def focus(progress: list[TrackProgress], limit: int = 3) -> list[tuple[TrackProgress, Trend]]:
    """The rubric items furthest below the bar lately. Ties go to the ones getting worse."""
    below = [(p, t) for p in progress for t in p.criteria if t.recent < BAR]
    below.sort(key=lambda pt: (pt[1].recent, pt[1].change or 0))
    return below[:limit]
