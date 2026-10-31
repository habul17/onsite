import pytest

from onsite import bank
from onsite.models import Level, Track


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
