from onsite.voice import listen


class FakeWhisper:
    def __init__(self, *texts):
        self.texts = texts
        self.kwargs = None

    def transcribe(self, samples, **kwargs):
        self.kwargs = kwargs
        segments = [type("Segment", (), {"text": f" {t} "}) for t in self.texts]
        return iter(segments), None


def transcriber(fake):
    t = listen.Transcriber(model="base.en")
    t._model = fake
    return t


def test_prompt_ends_with_the_question_so_it_survives_truncation():
    prompt = listen.whisper_prompt("Design a\n  rate limiter.")
    assert prompt.endswith(" Design a rate limiter.")
    assert "Postgres" in prompt and "Kafka" in prompt


def test_transcribe_joins_segments_and_primes_with_the_question():
    fake = FakeWhisper("I'd use a token bucket.", "Each user gets ten requests a second.")
    text = transcriber(fake).transcribe([0.0] * 16_000, context="Design a rate limiter.")
    assert text == "I'd use a token bucket. Each user gets ten requests a second."
    assert fake.kwargs["initial_prompt"].endswith("Design a rate limiter.")
    assert fake.kwargs["vad_filter"] is True
    assert fake.kwargs["language"] == "en"


def test_very_short_recordings_are_treated_as_silence():
    fake = FakeWhisper("Thank you.")
    assert transcriber(fake).transcribe([0.0] * 100) == ""
    assert fake.kwargs is None


def test_model_can_be_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("ONSITE_WHISPER_MODEL", "small.en")
    assert listen.Transcriber().model_name == "small.en"
