import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from onsite.llm import LLMError  # noqa: E402
from onsite.models import Track  # noqa: E402
from onsite.web.app import create_app  # noqa: E402
from test_cli import ANSWER, MOVE_ON, track_grade  # noqa: E402
from test_web_rounds import CODE, PROBE  # noqa: E402

START = {"track": "coding", "level": "senior", "seed": 1}


def client(llm, tmp_path, host="127.0.0.1"):
    return TestClient(create_app(llm, tmp_path / "reports"), base_url=f"http://{host}")


def test_the_page_and_options(tmp_path):
    c = client(None, tmp_path)
    page = c.get("/")
    assert page.status_code == 200
    for asset in ["/static/style.css", "/static/editor.js", "/static/app.js"]:
        assert asset in page.text and c.get(asset).status_code == 200
    options = c.get("/api/options").json()
    assert [t["id"] for t in options["tracks"]] == ["behavioral", "coding", "system_design"]
    assert options["levels"] == ["junior", "mid", "senior", "staff"]
    assert all(n >= 3 for t in options["tracks"] for n in t["questions"].values())


def test_a_whole_round_over_http(tmp_path, fake_llm):
    fake = fake_llm(PROBE, MOVE_ON, track_grade(Track.CODING, 3))
    c = client(fake, tmp_path)

    started = c.post("/api/rounds", json=START).json()
    assert started["prompt"] in started["message"]
    assert (started["question"], started["questions"], started["done"]) == (1, 1, False)

    rid = started["id"]
    follow_up = c.post(f"/api/rounds/{rid}/answer", json={"text": "Sort first.", "code": CODE}).json()
    assert follow_up["message"] == "Why a heap?" and not follow_up["done"]
    assert "My code so far" in fake.calls[0]["prompt"]

    last = c.post(f"/api/rounds/{rid}/answer", json={"text": ANSWER, "code": CODE}).json()
    assert last["done"] and "That's all the questions I have" in last["message"]
    assert fake.calls[1]["prompt"].count("My code so far") == 1  # unchanged code isn't sent again

    finished = c.post(f"/api/rounds/{rid}/finish").json()
    assert finished["report"]["mean"] == 3
    saved = tmp_path / "reports" / finished["saved_to"].split("/")[-1]
    assert json.loads(saved.with_suffix(".json").read_text())["track"] == "coding"


def test_other_host_names_are_refused(tmp_path):
    # Stops another site pointing its own domain at this server and using your API key.
    assert client(None, tmp_path, host="evil.example").get("/api/options").status_code == 400


def test_a_form_on_another_site_cannot_start_a_round(tmp_path, fake_llm):
    # Browsers send plain forms across sites without asking. The API only takes JSON.
    c = client(fake_llm(), tmp_path)
    assert c.post("/api/rounds", data=START).status_code == 422


def test_errors_come_back_with_a_reason(tmp_path, fake_llm):
    c = client(fake_llm(MOVE_ON, track_grade(Track.CODING, 3)), tmp_path)
    assert c.post("/api/rounds/nope/answer", json={"text": "hi"}).status_code == 404
    assert c.post("/api/rounds", json={**START, "level": "intern"}).status_code == 422

    too_many = c.post("/api/rounds", json={"track": "system_design", "level": "junior", "questions": 10})
    assert too_many.status_code == 400 and "only" in too_many.json()["detail"]

    rid = c.post("/api/rounds", json=START).json()["id"]
    assert c.post(f"/api/rounds/{rid}/finish").status_code == 400  # nothing answered yet
    assert c.post(f"/api/rounds/{rid}/answer", json={"text": "  "}).status_code == 400
    c.post(f"/api/rounds/{rid}/answer", json={"text": ANSWER})
    c.post(f"/api/rounds/{rid}/finish")
    over = c.post(f"/api/rounds/{rid}/answer", json={"text": "One more thing."})
    assert over.status_code == 409 and "graded" in over.json()["detail"]


class DownLLM:
    def structured(self, **_):
        raise LLMError("The model is overloaded. Try again in a minute.")


def test_a_model_failure_is_a_502_and_the_round_carries_on(tmp_path):
    c = client(DownLLM(), tmp_path)
    rid = c.post("/api/rounds", json=START).json()["id"]

    failed = c.post(f"/api/rounds/{rid}/answer", json={"text": ANSWER})
    assert failed.status_code == 502 and "overloaded" in failed.json()["detail"]
    assert c.post(f"/api/rounds/{rid}/skip").status_code == 200
