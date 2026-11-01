"""Turns a spoken answer into text with Whisper, running on your machine."""

import os

DEFAULT_MODEL = "base.en"

# Whisper reads the prompt as text that came just before the audio, so it nudges the spelling of words it
# would otherwise guess at ("post-gres", "cough-ka", "big oh of n").
GLOSSARY = (
    "API, REST, gRPC, JSON, SQL, NoSQL, Postgres, MySQL, Redis, Kafka, Kubernetes, Docker, AWS, S3, CDN, "
    "QPS, p99 latency, sharding, replication, idempotency, cache invalidation, load balancer, hash map, "
    "heap, BFS, DFS, LRU cache, two pointers, sliding window, O(n), O(log n), O(n log n)."
)


def whisper_prompt(context: str = "") -> str:
    """Common tech terms, then the question being answered.

    The question goes last: Whisper keeps only the last 223 tokens, and the words nearest the audio count most.
    """
    context = " ".join(context.split())
    return f"{GLOSSARY} {context}".strip()


class Transcriber:
    def __init__(self, model: str | None = None):
        self.model_name = model or os.environ.get("ONSITE_WHISPER_MODEL", DEFAULT_MODEL)
        self._model = None

    def load(self) -> None:
        """Load the model, downloading it the first time (about 150 MB for base.en)."""
        if self._model is None:
            from faster_whisper import WhisperModel

            # CPU on purpose: a GPU build without the right CUDA libraries fails halfway through a round.
            self._model = WhisperModel(
                self.model_name, device="cpu", compute_type="int8", cpu_threads=physical_cores()
            )

    def transcribe(self, samples, context: str = "") -> str:
        """Samples are float32 mono at 16 kHz. Returns "" when nothing was said."""
        if len(samples) < 16_000 // 2:
            return ""
        self.load()
        segments, _ = self._model.transcribe(
            samples,
            language="en",
            beam_size=5,
            vad_filter=True,  # skips silence, which is where Whisper makes up words
            initial_prompt=whisper_prompt(context),
        )
        return " ".join(s.text.strip() for s in segments).strip()


def physical_cores() -> int:
    # Running on every hyperthread is slower than one thread per core for these models.
    return max(1, (os.cpu_count() or 2) // 2)
