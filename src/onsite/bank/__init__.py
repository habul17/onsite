"""The question bank: one YAML file per track with its rubric, level bar and questions."""

import random
from functools import cache
from importlib.resources import files

import yaml

from onsite.models import Level, Question, Track, TrackBank

# Coding and design rounds are a long back-and-forth, so they get many more turns per question.
DEFAULT_QUESTIONS = {Track.BEHAVIORAL: 3, Track.CODING: 1, Track.SYSTEM_DESIGN: 1}
DEFAULT_FOLLOW_UPS = {Track.BEHAVIORAL: 3, Track.CODING: 12, Track.SYSTEM_DESIGN: 15}


@cache
def load(track: Track) -> TrackBank:
    raw = yaml.safe_load(files(__package__).joinpath(f"{track.value}.yaml").read_text())
    for q in raw["questions"]:
        q["track"] = raw["track"]
    return TrackBank.model_validate(raw)


def pick(track: Track, level: Level, count: int, seed: int | None = None) -> list[Question]:
    """Pick `count` different questions that suit the level, in random order."""
    pool = [q for q in load(track).questions if level in q.levels]
    if count > len(pool):
        raise ValueError(f"only {len(pool)} {track} questions for {level}, asked for {count}")
    return random.Random(seed).sample(pool, count)
