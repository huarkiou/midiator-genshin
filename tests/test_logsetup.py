"""The input path must never wait for the console to write a line."""

from __future__ import annotations

import io
import logging
import threading
import time

from midiator.logsetup import QUEUE_SIZE, background_logging, level_for


class RecordingStream(io.TextIOBase):
    """Collects writes, and remembers which thread made them."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.threads: list[str] = []
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            self.lines.append(text)
            self.threads.append(threading.current_thread().name)
        return len(text)

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self.lines)


class BlockingStream(RecordingStream):
    """A console that hangs: the first write waits for the test to let it go."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, text: str) -> int:
        self.entered.set()
        self.release.wait(5.0)
        return super().write(text)


def test_lines_are_written_from_a_background_thread() -> None:
    stream = RecordingStream()
    with background_logging(quiet=False, verbose=False, stream=stream):
        logging.getLogger("test").info("hello")
    assert "hello" in stream.text
    assert stream.threads
    assert stream.threads[0] != threading.current_thread().name


def test_a_blocked_console_does_not_block_the_caller() -> None:
    """The point of the whole module: logging must never wait for a console."""
    stream = BlockingStream()
    with background_logging(quiet=False, verbose=False, stream=stream):
        logging.getLogger("test").info("first")
        assert stream.entered.wait(2.0), "the writer thread should be stuck in write()"

        started = time.perf_counter()
        for index in range(200):
            logging.getLogger("test").info("later %d", index)
        elapsed = time.perf_counter() - started

        stream.release.set()

    assert elapsed < 1.0, f"logging waited for the console: {elapsed:.3f}s"


def test_stop_flushes_everything_that_was_queued() -> None:
    stream = RecordingStream()
    with background_logging(quiet=False, verbose=False, stream=stream):
        for index in range(50):
            logging.getLogger("test").info("line %d", index)

    written = [line for line in stream.text.splitlines() if line.startswith("line ")]
    assert len(written) == 50
    assert written[-1] == "line 49"


def test_the_queue_never_grows_without_limit() -> None:
    """Past the buffer the meter drops lines instead of blocking the caller."""
    stream = BlockingStream()
    with background_logging(quiet=False, verbose=False, stream=stream) as setup:
        logging.getLogger("test").info("first")
        assert stream.entered.wait(2.0)
        for index in range(QUEUE_SIZE + 50):
            logging.getLogger("test").info("overflow %d", index)
        assert setup.dropped > 0
        stream.release.set()


def test_levels_follow_the_switches() -> None:
    assert level_for(quiet=False, verbose=False) == logging.INFO
    assert level_for(quiet=True, verbose=False) == logging.WARNING
    assert level_for(quiet=False, verbose=True) == logging.DEBUG
    assert level_for(quiet=True, verbose=True) == logging.DEBUG


def test_quiet_hides_information_but_keeps_warnings() -> None:
    stream = RecordingStream()
    with background_logging(quiet=True, verbose=False, stream=stream):
        logging.getLogger("test").info("too quiet")
        logging.getLogger("test").warning("important")
    assert "too quiet" not in stream.text
    assert "important" in stream.text


def test_the_handler_is_detached_after_stop() -> None:
    stream = RecordingStream()
    setup = background_logging(quiet=False, verbose=False, stream=stream)
    setup.start()
    assert setup._handler in logging.getLogger().handlers
    setup.stop()
    assert setup._handler not in logging.getLogger().handlers
