"""Gives the interviewer a voice with Kokoro, running on your machine."""

import hashlib
import importlib.metadata
import os
import re
import shutil
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn

from onsite.voice.listen import physical_cores

DEFAULT_VOICE = "af_heart"
RATE = 24_000
PAUSE_SECONDS = 0.3

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL = "kokoro-v1.0.int8.onnx"
VOICES = "voices-v1.0.bin"
SHA256 = {
    MODEL: "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    VOICES: "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}

SPOKEN = {"e.g.": "for example", "i.e.": "that is", "vs.": "versus", "etc.": "and so on"}


class Speaker:
    def __init__(self, voice: str | None = None, speed: float = 1.0):
        self.voice = voice or os.environ.get("ONSITE_VOICE", DEFAULT_VOICE)
        self.speed = speed
        self._kokoro = None

    def load(self) -> None:
        """Load the voice model, downloading it the first time (about 120 MB)."""
        if self._kokoro is not None:
            return
        import onnxruntime as rt
        from kokoro_onnx import EspeakConfig, Kokoro

        folder = download_models()
        options = rt.SessionOptions()
        options.intra_op_num_threads = physical_cores()
        session = rt.InferenceSession(str(folder / MODEL), options, providers=["CPUExecutionProvider"])
        kokoro = Kokoro.from_session(
            session, str(folder / VOICES), espeak_config=EspeakConfig(data_path=espeak_data_path())
        )
        if self.voice not in kokoro.get_voices():
            raise ValueError(f"No voice called {self.voice!r}. Try one of: {', '.join(kokoro.get_voices())}")
        self._kokoro = kokoro

    def say(self, text: str) -> None:
        from onsite.voice.audio import play

        self.load()
        play(self.speech(text), RATE)

    def speech(self, text: str) -> Iterator:
        """One chunk of audio per sentence, so the first sentence plays while the next one is made."""
        import numpy as np

        pause = np.zeros(int(RATE * PAUSE_SECONDS), dtype=np.float32)
        for sentence in sentences(speakable(text)):
            samples, _ = self._kokoro.create(sentence, voice=self.voice, speed=self.speed, lang="en-us")
            yield np.concatenate([samples.astype(np.float32), pause])


def speakable(text: str) -> str:
    """Rewrite the bits a voice model reads badly, like O(n log n) and code formatting."""
    text = re.sub(r"\bO\(([^()]*)\)", lambda m: "O of " + m.group(1).replace("*", " times "), text)
    text = re.sub(r"(\w)\^2\b", r"\1 squared", text)
    text = re.sub(r"(\w)\^(\w+)", r"\1 to the \2", text)
    for short, spoken in SPOKEN.items():
        text = text.replace(short, spoken)
    text = re.sub(r"[`*#]", "", text).replace("_", " ")
    return " ".join(text.split())


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "onsite"


def download_models(folder: Path | None = None) -> Path:
    folder = folder or cache_dir() / "kokoro"
    folder.mkdir(parents=True, exist_ok=True)
    missing = [name for name in SHA256 if not (folder / name).exists()]
    if missing:
        columns = (TextColumn("{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn())
        with Progress(*columns) as progress:
            for name in missing:
                download(RELEASE + name, folder / name, SHA256[name], progress)
    return folder


def download(url: str, dest: Path, sha256: str, progress: Progress) -> None:
    """Download to a .part file and only keep it if the checksum matches."""
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=30) as response, part.open("wb") as f:
        task = progress.add_task(f"Downloading {dest.name}", total=int(response.headers.get("Content-Length", 0)) or None)
        while chunk := response.read(1 << 20):
            f.write(chunk)
            digest.update(chunk)
            progress.advance(task, len(chunk))
    if digest.hexdigest() != sha256:
        part.unlink()
        raise RuntimeError(f"{dest.name} did not download correctly. Run again to retry.")
    part.replace(dest)


def espeak_data_path() -> str:
    """espeak-ng can't use a data folder whose path is 160 bytes or longer, so copy it somewhere short."""
    import espeakng_loader

    path = espeakng_loader.get_data_path()
    if len(path.encode()) < 150:
        return path

    short = cache_dir() / f"espeak-ng-data-{importlib.metadata.version('espeakng-loader')}"
    if not short.exists():
        tmp = short.with_name(short.name + ".part")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(path, tmp)
        tmp.rename(short)
    return str(short)
