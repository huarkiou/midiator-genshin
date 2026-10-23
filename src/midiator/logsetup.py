"""Logging that keeps console I/O out of the input hot path.

A MIDI message and the key press it causes are microseconds apart, so anything
that blocks in between is felt as latency. Writing a line to the console is
milliseconds of work, and it blocks outright while the window has a selection:
exactly the wrong thing to do between reading a note and sending a key.

So the root logger installed here never writes anything itself. It fills in the
message and drops the record on a queue; a daemon thread does the writing. The
cost on the input path falls to a queue put, and a stuck console can only make
the log lag behind, never the music.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from logging.handlers import QueueHandler
from types import TracebackType
from typing import Protocol, TextIO, cast

#: Records buffered before the writer is considered stuck. Past this the newest
#: lines are dropped: reporting on the input path must never hold up the input
#: path.
QUEUE_SIZE = 1000

#: Seconds the writer sleeps between checks for work, which bounds how quickly
#: ``stop()`` is noticed while still costing nothing when idle.
POLL_SECONDS = 0.1

#: Seconds to wait for queued lines at shutdown. A console that is stuck (a
#: selection is the usual reason) must not keep the process alive.
SHUTDOWN_TIMEOUT = 2.0


class LogStream(Protocol):
    """The part of a text stream a log handler uses.

    Narrower than ``typing.TextIO`` so a test can pass a stand-in that only
    collects what was written.
    """

    def write(self, text: str, /) -> int:
        """Write ``text``, returning how many characters were accepted."""

    def flush(self) -> None:
        """Push anything buffered to the real destination."""


class _DropWhenFull(QueueHandler):
    """A :class:`QueueHandler` with a soft cap that never blocks or writes.

    ``QueueHandler`` reports a full queue through ``handleError``, which prints
    to stderr *from the calling thread*. Dropping quietly keeps the promise that
    a log call never touches a stream.
    """

    def __init__(self, records: queue.Queue[logging.LogRecord]) -> None:
        super().__init__(records)
        self.records = records
        self.dropped = 0

    def enqueue(self, record: logging.LogRecord) -> None:
        # qsize() is only an estimate under concurrency, which is all a cap needs
        # to be: the bound matters, not the exact count.
        if self.records.qsize() >= QUEUE_SIZE:
            self.dropped += 1
            return
        self.records.put(record)


class _LogWriter(threading.Thread):
    """Writes queued records until asked to finish, then drains the rest."""

    def __init__(self, records: queue.Queue[logging.LogRecord], handler: logging.Handler) -> None:
        super().__init__(name="midiator-log", daemon=True)
        self.records = records
        self.handler = handler
        # Not _stop: that name is taken by threading.Thread's own machinery.
        self._stop_requested = threading.Event()

    def run(self) -> None:
        while True:
            try:
                record = self.records.get(timeout=POLL_SECONDS)
            except queue.Empty:
                if self._stop_requested.is_set():
                    self._drain()
                    return
                continue
            self.handler.handle(record)

    def _drain(self) -> None:
        """Write everything still queued."""
        while True:
            try:
                self.handler.handle(self.records.get_nowait())
            except queue.Empty:
                return

    def finish(self, timeout: float) -> None:
        """Drain what is queued and stop, giving up after ``timeout`` seconds.

        Giving up leaves a daemon thread behind, which the interpreter discards;
        the alternative is an application that cannot exit because its console
        is wedged.
        """
        self._stop_requested.set()
        self.join(timeout=timeout)


class BackgroundLogging:
    """A background log writer, installed on the root logger.

    Use it as a context manager around everything that logs: leaving the block
    flushes what is still queued (up to :data:`SHUTDOWN_TIMEOUT`) and stops the
    writer, so the last line of a session is not lost.
    """

    def __init__(self, level: int, stream: LogStream) -> None:
        self.level = level
        self._root = logging.getLogger()
        self._records: queue.Queue[logging.LogRecord] = queue.Queue()
        self._handler = _DropWhenFull(self._records)
        self._stream = logging.StreamHandler(cast(TextIO, stream))
        self._stream.setFormatter(logging.Formatter("%(message)s"))
        self._writer: _LogWriter | None = None

    def __enter__(self) -> BackgroundLogging:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> BackgroundLogging:
        """Take over the root logger and start the writer thread."""
        if self._writer is not None:
            return self
        self._root.setLevel(self.level)
        for existing in self._root.handlers[:]:
            self._root.removeHandler(existing)
        self._root.addHandler(self._handler)
        self._writer = _LogWriter(self._records, self._stream)
        self._writer.start()
        return self

    def stop(self) -> None:
        """Write what is queued, detach, and stop the writer thread."""
        self._root.removeHandler(self._handler)
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.finish(SHUTDOWN_TIMEOUT)

    @property
    def dropped(self) -> int:
        """How many records were discarded because the writer fell behind."""
        return self._handler.dropped


def level_for(quiet: bool, verbose: bool) -> int:
    """Pick a log level from the command-line switches.

    ``--verbose`` wins over ``--quiet``, matching how the two are documented
    together.
    """
    if verbose:
        return logging.DEBUG
    if quiet:
        return logging.WARNING
    return logging.INFO


def background_logging(quiet: bool, verbose: bool, stream: LogStream | None = None) -> BackgroundLogging:
    """Build an unstarted :class:`BackgroundLogging` for the given switches.

    :param stream: Where lines end up. Defaults to the current ``sys.stdout``,
        resolved here rather than at import time so it follows redirection.
    """
    return BackgroundLogging(level_for(quiet, verbose), sys.stdout if stream is None else stream)
