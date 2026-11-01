"""Records from the microphone and plays through the speakers.

Uses miniaudio, which ships its own audio library, so nothing has to be installed on the system.
"""

import queue
import threading
import time
from collections.abc import Callable, Generator, Iterable

import miniaudio
import numpy as np

# Whisper expects 16 kHz mono.
RECORD_RATE = 16_000

_DONE = object()


def record(wait: Callable[[], object]) -> np.ndarray:
    """Record mono audio while `wait()` runs (for example, until Enter is pressed).

    Returns float32 samples between -1 and 1 at RECORD_RATE.
    """
    chunks: list[bytes] = []

    def sink() -> Generator[None, bytes, None]:
        while True:
            chunks.append(bytes((yield)))

    gen = sink()
    next(gen)
    with miniaudio.CaptureDevice(
        input_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=RECORD_RATE, app_name="onsite"
    ) as device:
        device.start(gen)
        wait()
    return to_float(b"".join(chunks))


def play(chunks: Iterable[np.ndarray], rate: int) -> None:
    """Play float32 mono chunks as one stream and block until they finish.

    Chunks are pulled in a background thread, so a slow producer (like a voice model making the next
    sentence) runs while the current chunk plays.
    """
    pending: queue.Queue = queue.Queue()
    errors: list[BaseException] = []

    def produce() -> None:
        try:
            for chunk in chunks:
                pending.put(to_int16(chunk))
        except Exception as e:  # handed to the main thread below
            errors.append(e)
        finally:
            pending.put(_DONE)

    threading.Thread(target=produce, daemon=True).start()

    finished = threading.Event()
    gen = feed(pending, finished)
    next(gen)
    with miniaudio.PlaybackDevice(
        output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=rate, app_name="onsite"
    ) as device:
        device.start(gen)
        finished.wait()
        # The last chunk is still in the device buffer when the generator ends.
        time.sleep(2 * device.buffersize_msec / 1000)

    if errors:
        raise errors[0]


def feed(pending: queue.Queue, finished: threading.Event) -> Generator[bytes, int, None]:
    """Playback callback: sent how many frames the device wants, yields up to that many.

    Yields silence while the next chunk isn't ready yet, so the stream never stalls.
    """
    buffer = np.zeros(0, dtype=np.int16)
    done = False
    wanted = yield b""
    while True:
        while not done and len(buffer) < wanted:
            try:
                item = pending.get_nowait()
            except queue.Empty:
                break
            if item is _DONE:
                done = True
            else:
                buffer = np.concatenate([buffer, item])

        if done and not len(buffer):
            finished.set()
            return

        out, buffer = buffer[:wanted], buffer[wanted:]
        if len(out) < wanted and not done:
            out = np.concatenate([out, np.zeros(wanted - len(out), dtype=np.int16)])
        wanted = yield out.tobytes()


def is_quiet(samples: np.ndarray, threshold: float = 0.01) -> bool:
    """True when the loudest sample is under about -40 dB, which usually means a muted or wrong microphone."""
    return not len(samples) or float(np.max(np.abs(samples))) < threshold


def to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def to_int16(samples: np.ndarray) -> np.ndarray:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
