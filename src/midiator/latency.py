"""A live latency table: what MIDIator itself costs per note.

The numbers here are only the delay that belongs to this program: from the
moment the supervisor hands a MIDI message over, to the moment ``SendInput`` has
returned. Everything else -- the USB adapter, the Windows MIDI stack, the game's
frame pacing, the audio output -- happens outside these measurements, and that is
what makes the table useful: when it reads 0.2 ms, the delay people feel is not
being added here.

``record_internal`` runs on the input thread and does nothing but append a
float, so measuring never becomes the thing being measured. Rows are printed by
a background thread.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from midiator.engine import KeySender
from midiator.windows.keyboard import Key

#: Samples kept per measurement between two rows. Enough for a dense passage,
#: bounded so a very fast player cannot grow the meter without limit.
SAMPLE_LIMIT = 4096

#: Fraction of the distribution reported as the tail.
TAIL = 0.95


def _percentile(ordered: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an already sorted list of positive values."""
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


@dataclass(frozen=True, slots=True)
class LatencyRow:
    """One line of the live latency table, in milliseconds."""

    events: int
    """How many MIDI events were timed in this row."""
    internal_p50_ms: float
    internal_p95_ms: float
    internal_max_ms: float
    send_p50_ms: float | None = None
    send_max_ms: float | None = None

    def format(self) -> str:
        """Render the row for a console."""
        line = (
            f"{self.events:3d} ev  "
            f"internal p50 {self.internal_p50_ms:6.2f}ms "
            f"p95 {self.internal_p95_ms:6.2f}ms "
            f"max {self.internal_max_ms:6.2f}ms"
        )
        if self.send_p50_ms is not None and self.send_max_ms is not None:
            line += f"  SendInput p50 {self.send_p50_ms:6.2f}ms max {self.send_max_ms:6.2f}ms"
        return line


class LatencyMeter:
    """Times the work MIDIator does per message and prints it once a second.

    :param interval: Seconds between rows.
    :param stream: Where rows are written. Defaults to ``sys.stdout``.
    :param clock: Monotonic clock, injectable for tests.
    """

    def __init__(
        self,
        interval: float = 1.0,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.interval = interval
        self._stream = sys.stdout if stream is None else stream
        self._clock = clock
        self._internal: deque[float] = deque(maxlen=SAMPLE_LIMIT)
        self._send: deque[float] = deque(maxlen=SAMPLE_LIMIT)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- live table -------------------------------------------------------

    @property
    def banner(self) -> str:
        """The line printed when the meter starts."""
        return (
            f"Latency meter: one row every {self.interval:g}s. "
            "'internal' is MIDIator's own time per note, from MIDI message to key sent."
        )

    def start(self) -> LatencyMeter:
        """Print the banner and start emitting rows in the background."""
        if self._thread is not None:
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="midiator-latency", daemon=True)
        self._thread.start()
        self._write(self.banner)
        return self

    def stop(self) -> None:
        """Stop emitting rows, reporting what was collected since the last one."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self.interval + 1.0)
        self._print_row()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._print_row()

    def _print_row(self) -> None:
        row = self.take_row()
        if row is not None:
            self._write(row.format())

    def _write(self, line: str) -> None:
        print(line, file=self._stream, flush=True)

    # -- measurement ------------------------------------------------------

    def now(self) -> float:
        """The meter's clock, so callers time things on the same scale."""
        return self._clock()

    def record_internal(self, seconds: float) -> None:
        """Record how long MIDIator took for one message, end to end."""
        self._internal.append(seconds)

    def record_send(self, seconds: float) -> None:
        """Record how long one ``SendInput`` call took on its own."""
        self._send.append(seconds)

    def take_row(self) -> LatencyRow | None:
        """Build a row from the samples collected since the last one.

        Returns ``None`` when no message was seen, so an idle player gets no
        rows and the console stays quiet.
        """
        internal = _drain(self._internal)
        send = _drain(self._send)
        if not internal:
            return None
        internal.sort()
        send.sort()
        return LatencyRow(
            events=len(internal),
            internal_p50_ms=_percentile(internal, 0.5) * 1000.0,
            internal_p95_ms=_percentile(internal, TAIL) * 1000.0,
            internal_max_ms=internal[-1] * 1000.0,
            send_p50_ms=_percentile(send, 0.5) * 1000.0 if send else None,
            send_max_ms=send[-1] * 1000.0 if send else None,
        )


def _drain(samples: deque[float]) -> list[float]:
    """Empty ``samples`` into a list."""
    taken = list(samples)
    samples.clear()
    return taken


class TimedSender:
    """A :class:`~midiator.engine.KeySender` that reports its own cost.

    Wraps the real sender so the table can separate "MIDIator is busy" from
    "``SendInput`` itself took 30 ms", which is what a slow input hook or a
    busy game looks like.
    """

    def __init__(self, inner: KeySender, meter: LatencyMeter) -> None:
        self.inner = inner
        self.meter = meter

    def send(self, key: Key, pressed: bool) -> None:
        """Press or release ``key``, recording how long it took."""
        started = self.meter.now()
        try:
            self.inner.send(key, pressed)
        finally:
            self.meter.record_send(self.meter.now() - started)
