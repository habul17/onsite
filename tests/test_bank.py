import re

import pytest

from onsite import bank, cli
from onsite.models import Level, Track

QUESTION_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@pytest.mark.parametrize("track", list(Track))
def test_every_track_loads(track):
    tb = bank.load(track)
    assert tb.track == track
    assert set(tb.level_bar) == set(Level)
    assert all(q.track == track for q in tb.questions)


@pytest.mark.parametrize("track", list(Track))
def test_ids_are_unique(track):
    tb = bank.load(track)
    question_ids = [q.id for q in tb.questions]
    criterion_ids = [c.id for c in tb.rubric]
    assert len(question_ids) == len(set(question_ids))
    assert len(criterion_ids) == len(set(criterion_ids))


@pytest.mark.parametrize("track", list(Track))
def test_every_question_is_complete(track):
    tb = bank.load(track)
    for q in tb.questions:
        assert QUESTION_ID.match(q.id), q.id
        assert q.title.strip() and q.prompt.strip(), q.id
        # The grader needs enough to judge against, and the interviewer needs somewhere to dig.
        assert len(q.strong_signals) >= 3, q.id
        assert len(q.follow_ups) >= 2, q.id
        assert len(set(q.strong_signals)) == len(q.strong_signals), q.id
    titles = [q.title.lower() for q in tb.questions]
    assert len(titles) == len(set(titles))


@pytest.mark.parametrize("track", list(Track))
def test_levels_have_no_gaps(track):
    # A question that suits juniors and staff suits everyone in between too.
    order = list(Level)
    for q in bank.load(track).questions:
        assert q.levels, q.id
        spots = sorted(order.index(lv) for lv in set(q.levels))
        assert spots == list(range(spots[0], spots[-1] + 1)), q.id


@pytest.mark.parametrize("level", list(Level))
@pytest.mark.parametrize("track", list(Track))
def test_every_level_has_three_rounds_without_a_repeat(track, level):
    rounds = cli.pick_loop([track] * 3, level, seed=0)
    ids = [q.id for r in rounds for q in r]
    assert len(ids) == 3 * cli.DEFAULT_QUESTIONS[track]
    assert len(ids) == len(set(ids))
    assert all(level in q.levels for r in rounds for q in r)


def test_pick_is_repeatable_with_a_seed():
    first = bank.pick(Track.BEHAVIORAL, Level.SENIOR, 3, seed=7)
    second = bank.pick(Track.BEHAVIORAL, Level.SENIOR, 3, seed=7)
    assert [q.id for q in first] == [q.id for q in second]
    assert len({q.id for q in first}) == 3


def test_pick_refuses_more_than_the_pool():
    with pytest.raises(ValueError):
        bank.pick(Track.SYSTEM_DESIGN, Level.MID, 99)


def test_lookup_by_id():
    assert bank.load(Track.CODING).question("lru-cache").title == "LRU cache"
    with pytest.raises(KeyError):
        bank.load(Track.CODING).question("nope")
