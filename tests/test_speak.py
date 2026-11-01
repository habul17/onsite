import hashlib
import io

import pytest
from rich.progress import Progress

from onsite.voice import speak


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("That runs in O(n log n) time.", "That runs in O of n log n time."),
        ("Is O(n*m) okay?", "Is O of n times m okay?"),
        ("Why not n^2 or 2^k?", "Why not n squared or 2 to the k?"),
        ("Walk me through `max_heap`.", "Walk me through max heap."),
        ("Pick one, e.g. Redis vs. Memcached.", "Pick one, for example Redis versus Memcached."),
        ("**Okay.**\n\nNext question.", "Okay. Next question."),
    ],
)
def test_speakable_rewrites_what_a_voice_reads_badly(text, spoken):
    assert speak.speakable(text) == spoken


def test_sentences_split_on_end_punctuation():
    assert speak.sentences("Okay. Why that choice? Tell me more!  ") == ["Okay.", "Why that choice?", "Tell me more!"]
    assert speak.sentences("") == []


class FakeResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def test_download_keeps_the_file_only_if_the_checksum_matches(tmp_path, monkeypatch):
    data = b"model bytes"
    monkeypatch.setattr(speak.urllib.request, "urlopen", lambda url, timeout: FakeResponse(data))
    dest = tmp_path / "model.onnx"

    with pytest.raises(RuntimeError, match="did not download correctly"):
        speak.download("https://example.com/model.onnx", dest, "0" * 64, Progress(disable=True))
    assert list(tmp_path.iterdir()) == []

    speak.download("https://example.com/model.onnx", dest, hashlib.sha256(data).hexdigest(), Progress(disable=True))
    assert dest.read_bytes() == data
    assert list(tmp_path.iterdir()) == [dest]


def test_download_models_skips_files_already_there(tmp_path, monkeypatch):
    for name in speak.SHA256:
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setattr(speak, "download", lambda *a: pytest.fail("should not download"))
    assert speak.download_models(tmp_path) == tmp_path


def test_speech_makes_one_chunk_per_sentence_with_a_pause():
    np = pytest.importorskip("numpy")

    class FakeKokoro:
        def __init__(self):
            self.said = []

        def create(self, text, voice, speed, lang):
            self.said.append(text)
            return np.ones(10, dtype=np.float32), speak.RATE

    speaker = speak.Speaker(voice="af_heart")
    speaker._kokoro = FakeKokoro()
    chunks = list(speaker.speech("Okay. What's the O(n) part?"))

    assert speaker._kokoro.said == ["Okay.", "What's the O of n part?"]
    assert len(chunks) == 2
    assert len(chunks[0]) == 10 + int(speak.RATE * speak.PAUSE_SECONDS)
    assert chunks[0][-1] == 0
