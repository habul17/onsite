"""A small wrapper around the model so the rest of the code only asks for typed answers."""

import os
from typing import Literal, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

Effort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_MODEL = "claude-opus-5-5"

# Models that accept `fallbacks: "default"`, which reruns a declined request on another model.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def structured(self, *, system: str, prompt: str, schema: type[T], effort: Effort = "medium") -> T: ...


class AnthropicLLM:
    def __init__(self, model: str | None = None, client: anthropic.Anthropic | None = None):
        self.model = model or os.environ.get("ONSITE_MODEL", DEFAULT_MODEL)
        self.client = client or anthropic.Anthropic()

    def structured(self, *, system: str, prompt: str, schema: type[T], effort: Effort = "medium") -> T:
        extra = {}
        if self.model in FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": effort},
                cache_control={"type": "ephemeral"},
                **extra,
            )
        except anthropic.AuthenticationError as e:
            raise LLMError("API key is missing or invalid. Set ANTHROPIC_API_KEY.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limited by the API. Wait a minute and try again.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the API. Check your internet connection.") from e

        if response.stop_reason == "refusal":
            raise LLMError("The model declined to answer this turn.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The reply was cut off before it finished.")
        if response.parsed_output is None:
            raise LLMError("The reply did not match the expected format.")
        return response.parsed_output
