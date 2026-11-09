import pytest
from pydantic import ValidationError

from onsite import bank, prompts
from onsite.grader import quote_found
from onsite.models import Level, Track
from onsite.whiteboard import Arrow, Board, Box, describe


def board():
    return Board(
        boxes=[
            Box(id="db", kind="database", label="Postgres", x=300, y=320),
            Box(id="app", kind="client", label="Mobile app", x=40, y=20),
            Box(id="api", kind="service", label="API  gateway", x=300, y=20),
            Box(id="cache", kind="cache", label="Redis", x=520, y=170),
        ],
        arrows=[
            Arrow(source="app", target="api", label="HTTPS"),
            Arrow(source="api", target="cache", label="read through"),
            Arrow(source="api", target="db"),
        ],
    )


def test_parts_are_listed_in_the_order_they_were_added():
    assert describe(board()) == (
        "Components:\n"
        "- Postgres (database)\n"
        "- Mobile app (client)\n"
        "- API gateway (service)\n"
        "- Redis (cache)\n"
        "Connections:\n"
        "- Mobile app -> API gateway: HTTPS\n"
        "- API gateway -> Redis: read through\n"
        "- API gateway -> Postgres"
    )


def test_moving_boxes_changes_nothing():
    moved = board()
    for i, b in enumerate(moved.boxes):
        b.x, b.y = 1000 - 200 * i, 50 * i
    assert describe(moved) == describe(board())


def test_an_empty_board_says_nothing():
    assert describe(Board()) == ""


def test_broken_and_repeated_arrows_are_dropped():
    b = board()
    b.arrows += [
        Arrow(source="api", target="gone"),
        Arrow(source="api", target="api"),
        Arrow(source="app", target="api", label="HTTPS"),
    ]
    assert describe(b) == describe(board())


def test_every_box_gets_a_unique_name():
    b = Board(
        boxes=[
            Box(id="1", kind="service", label="Worker", x=0, y=0),
            Box(id="2", kind="service", label="worker", x=200, y=0),
            Box(id="3", kind="queue", x=400, y=0),
        ],
        arrows=[Arrow(source="1", target="2")],
    )
    text = describe(b)
    assert "- Worker (service)\n- worker (2) (service)\n- Unnamed queue (queue)" in text
    assert "- Worker -> worker (2)" in text


def test_notes_are_listed_on_their_own():
    b = board()
    b.boxes.append(Box(id="n", kind="note", label="  100k writes / sec at peak ", x=0, y=500))
    b.boxes.append(Box(id="blank", kind="note", label="   ", x=0, y=600))
    b.arrows.append(Arrow(source="n", target="db"))
    text = describe(b)
    assert text.endswith("Notes:\n- 100k writes / sec at peak")
    assert text.count(" -> ") == 3  # the arrow from the note isn't a connection


def test_limits_keep_a_board_from_flooding_the_prompt():
    with pytest.raises(ValidationError):
        Board(boxes=[Box(id=str(i)) for i in range(61)])
    with pytest.raises(ValidationError):
        Box(id="x", label="a" * 81)
    with pytest.raises(ValidationError):
        Box(id="x", kind="spaceship")


def test_the_interviewer_and_grader_know_how_to_read_it():
    tb = bank.load(Track.SYSTEM_DESIGN)
    q = tb.questions[0]
    assert "My whiteboard so far:" in prompts.interviewer_system(tb, Level.SENIOR, q)
    assert "My whiteboard so far:" in prompts.grader_system(tb, Level.SENIOR, q)


def test_a_quote_from_the_board_passes_the_quote_check():
    said = "Start simple.\n\nMy whiteboard so far:\n" + describe(board())
    assert quote_found("API gateway -> Redis: read through", said)
