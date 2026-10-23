"""The live latency table: the numbers, the line it prints, and its thread."""

from __future__ import annotations

import io
import time
from collections.abc import Callable

import pytest

from midiator.latency import LatencyMeter, LatencyRow, TimedSender
from midiator.windows.keyboard import Key, lookup_key


class RecordingSender:
    """Records what was sent, and can fail on purpose."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.events: list[tuple[str, bool]] = []
        self.error = error

    def send(self, key: Key, pressed: bool) -> None:
        self.events.append((key.name, pressed))
        if self.error is not None:
            raise self.error


def fixed_clock(*values: float) -> Callable[[], float]:
    """A clock that returns ``values`` in order, then repeats the last one."""
    remaining = list(values)

    def clock() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return clock


class TestLatencyRow:
    def test_format_names_both_measurements(self) -> None:
        row = LatencyRow(
            events=12,
            internal_p50_ms=0.21,
            internal_p95_ms=0.35,
            internal_max_ms=0.42,
            send_p50_ms=0.04,
            send_max_ms=0.11,
        )
        line = row.format()
        assert "12 ev" in line
        assert "internal p50   0.21ms" in line
        assert "p95   0.35ms" in line
        assert "max   0.42ms" in line
        assert "SendInput p50   0.04ms" in line
        assert "max   0.11ms" in line

    def test_format_omits_sendinput_without_samples(self) -> None:
        row = LatencyRow(events=1, internal_p50_ms=0.2, internal_p95_ms=0.2, internal_max_ms=0.2)
        assert "SendInput" not in row.format()


class TestLatencyMeter:
    def test_reports_nearest_rank_percentiles(self) -> None:
        meter = LatencyMeter(stream=io.StringIO())
        for milliseconds in (1.0, 2.0, 3.0, 4.0):
            meter.record_internal(milliseconds / 1000.0)

        row = meter.take_row()

        assert row is not None
        assert row.events == 4
        assert row.internal_p50_ms == pytest.approx(3.0)
        assert row.internal_p95_ms == pytest.approx(4.0)
        assert row.internal_max_ms == pytest.approx(4.0)
        assert row.send_p50_ms is None
        assert row.send_max_ms is None

    def test_taking_a_row_clears_the_samples(self) -> None:
        meter = LatencyMeter(stream=io.StringIO())
        meter.record_internal(0.001)
        assert meter.take_row() is not None
        assert meter.take_row() is None

    def test_an_idle_player_gets_no_rows(self) -> None:
        assert LatencyMeter(stream=io.StringIO()).take_row() is None

    def test_send_samples_are_reported_separately(self) -> None:
        meter = LatencyMeter(stream=io.StringIO())
        meter.record_internal(0.002)
        meter.record_send(0.0005)
        meter.record_send(0.0007)

        row = meter.take_row()

        assert row is not None
        assert row.send_p50_ms == pytest.approx(0.7)
        assert row.send_max_ms == pytest.approx(0.7)

    def test_rows_are_printed_while_it_runs(self) -> None:
        stream = io.StringIO()
        meter = LatencyMeter(interval=0.02, stream=stream).start()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and " ev " not in stream.getvalue():
                meter.record_internal(0.0002)
                time.sleep(0.005)
        finally:
            meter.stop()

        output = stream.getvalue()
        assert "Latency meter:" in output
        assert " ev " in output

    def test_stop_reports_what_it_collected(self) -> None:
        # A long interval means no tick can fire, so only stop() can print.
        stream = io.StringIO()
        meter = LatencyMeter(interval=60.0, stream=stream)
        meter.start()
        meter.record_internal(0.0005)
        meter.stop()

        assert " ev " in stream.getvalue()

    def test_stop_is_idempotent(self) -> None:
        meter = LatencyMeter(interval=60.0, stream=io.StringIO())
        meter.start()
        meter.stop()
        meter.stop()
        assert meter._thread is None

    def test_a_run_that_measures_nothing_prints_only_the_banner(self) -> None:
        stream = io.StringIO()
        meter = LatencyMeter(interval=0.01, stream=stream)
        meter.start()
        time.sleep(0.05)
        meter.stop()
        assert " ev " not in stream.getvalue()


class TestTimedSender:
    def test_forwards_and_records_the_time_it_took(self) -> None:
        inner = RecordingSender()
        meter = LatencyMeter(stream=io.StringIO(), clock=fixed_clock(0.0, 0.5))
        sender = TimedSender(inner, meter)

        sender.send(lookup_key("A"), True)

        assert inner.events == [("A", True)]
        meter.record_internal(0.001)  # what the caller records for the message
        row = meter.take_row()
        assert row is not None
        assert row.send_p50_ms == pytest.approx(500.0)

    def test_records_the_time_even_when_sending_fails(self) -> None:
        inner = RecordingSender(error=RuntimeError("SendInput failed"))
        meter = LatencyMeter(stream=io.StringIO(), clock=fixed_clock(0.0, 0.25))
        sender = TimedSender(inner, meter)

        with pytest.raises(RuntimeError, match="SendInput failed"):
            sender.send(lookup_key("A"), False)

        meter.record_internal(0.001)
        row = meter.take_row()
        assert row is not None
        assert row.send_max_ms == pytest.approx(250.0)
