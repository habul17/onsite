"""The web app's HTTP API and page. It only answers requests made to your own machine."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from onsite import bank
from onsite.grader import RoundReport
from onsite.llm import LLM, LLMError
from onsite.models import Level, Track
from onsite.prompts import TRACK_NAMES
from onsite.web.rounds import NothingToGrade, RoundBusy, RoundNotFound, RoundOver, Rounds, WebRound

STATIC = Path(__file__).parent / "static"

# A web page on another site could point its own domain at 127.0.0.1 and call this API with your key.
# Only answering requests addressed to localhost stops that.
LOCAL_HOSTS = ["127.0.0.1", "localhost"]

# Status code for each thing that can go wrong, with the message shown to the candidate.
ERRORS: dict[type[Exception], int] = {
    RoundNotFound: 404,
    RoundBusy: 409,
    RoundOver: 409,
    NothingToGrade: 400,
    ValueError: 400,
    LLMError: 502,
}


class StartRequest(BaseModel):
    track: Track
    level: Level
    questions: int | None = Field(default=None, ge=1, le=10)
    seed: int | None = None


class AnswerRequest(BaseModel):
    text: str = Field(default="", max_length=20_000)
    code: str | None = Field(default=None, max_length=50_000, description="What is in the editor right now.")


class RoundState(BaseModel):
    id: str
    track: Track
    level: Level
    message: str = Field(description="What the interviewer just said.")
    question: int = Field(description="Which question the round is on, counting from 1.")
    questions: int
    title: str
    prompt: str = Field(description="The current question, so the page can keep it in view while you work.")
    done: bool


class Finished(BaseModel):
    report: RoundReport
    saved_to: str


class TrackOption(BaseModel):
    id: Track
    name: str
    questions: dict[Level, int]


class Options(BaseModel):
    tracks: list[TrackOption]
    levels: list[Level]


def create_app(llm: LLM, reports: Path) -> FastAPI:
    rounds = Rounds(llm, reports)
    app = FastAPI(title="onsite", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    for error, status in ERRORS.items():
        app.add_exception_handler(error, _handler(status))

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/options")
    def options() -> Options:
        tracks = []
        for t in Track:
            qs = bank.load(t).questions
            counts = {lv: sum(lv in q.levels for q in qs) for lv in Level}
            tracks.append(TrackOption(id=t, name=TRACK_NAMES[t].capitalize(), questions=counts))
        return Options(tracks=tracks, levels=list(Level))

    # Plain functions, not async: the model calls block, so FastAPI runs each one on a worker thread.
    @app.post("/api/rounds")
    def start(body: StartRequest) -> RoundState:
        r, opening = rounds.start(body.track, body.level, body.questions, body.seed)
        return state(r, opening)

    @app.post("/api/rounds/{round_id}/answer")
    def answer(round_id: str, body: AnswerRequest) -> RoundState:
        reply = rounds.answer(round_id, body.text, body.code)
        return state(rounds.get(round_id), reply)

    @app.post("/api/rounds/{round_id}/skip")
    def skip(round_id: str) -> RoundState:
        reply = rounds.skip(round_id)
        return state(rounds.get(round_id), reply)

    @app.post("/api/rounds/{round_id}/finish")
    def finish(round_id: str) -> Finished:
        r = rounds.finish(round_id)
        return Finished(report=r.report, saved_to=str(r.saved_to))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def state(r: WebRound, message: str) -> RoundState:
    question = r.interview.current.question
    return RoundState(
        id=r.id,
        track=r.track,
        level=r.level,
        message=message,
        question=r.question_number,
        questions=r.question_count,
        title=question.title,
        prompt=question.prompt.strip(),
        done=r.interview.done,
    )


def _handler(status: int):
    def handle(request: Request, exc: Exception) -> JSONResponse:
        message = "No round with that id. Rounds end when the server restarts." if status == 404 else str(exc)
        return JSONResponse({"detail": message}, status_code=status)

    return handle
