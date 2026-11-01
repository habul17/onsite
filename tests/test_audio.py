import queue
import threading

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("miniaudio")

from onsite.voice import audio  # noqa: E402


def pcm(*values):
    return np.array(values, dtype=np.int16)


def start(*chunks):
    q = queue.Queue()
    for c in chunks:
        q.put(c)
    finished = threading.Event()
    gen = audio.feed(q, finished)
    next(gen)
    return q, finished, gen


def frames(data):
    return np.frombuffer(data, dtype=np.int16).tolist()


def test_feed_plays_chunks_in_order_across_callbacks():
    _, finished, gen = start(pcm(1, 2, 3), pcm(4, 5), audio._DONE)
    assert frames(gen.send(2)) == [1, 2]
    assert frames(gen.send(2)) == [3, 4]
    assert frames(gen.send(2)) == [5]
    with pytest.raises(StopIteration):
        gen.send(2)
    assert finished.is_set()


def test_feed_pads_with_silence_while_the_next_chunk_is_being_made():
    q, finished, gen = start(pcm(7))
    assert frames(gen.send(3)) == [7, 0, 0]
    assert frames(gen.send(2)) == [0, 0]
    assert not finished.is_set()

    q.put(pcm(8, 9))
    q.put(audio._DONE)
    assert frames(gen.send(2)) == [8, 9]
    with pytest.raises(StopIteration):
        gen.send(2)
    assert finished.is_set()


def test_samples_convert_both_ways_and_clip():
    out = audio.to_int16(np.array([0.0, 0.5, -1.0, 2.0], dtype=np.float32))
    assert out.tolist() == [0, 16383, -32767, 32767]
    back = audio.to_float(out.tobytes())
    assert back.dtype == np.float32
    assert back[1] == pytest.approx(0.5, abs=1e-3)
