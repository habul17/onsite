import pytest


class FakeLLM:
    """Returns scripted replies in order and remembers every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def structured(self, *, system, prompt, schema, effort="medium"):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema, "effort": effort})
        reply = self.replies.pop(0)
        assert isinstance(reply, schema), f"expected {schema.__name__}, got {type(reply).__name__}"
        return reply


@pytest.fixture
def fake_llm():
    return FakeLLM
