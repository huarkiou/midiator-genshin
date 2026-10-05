"""Tests for MIDI hot-plug supervision: waiting, disconnects and reconnecting.

The supervisor is exercised against fake ports, so these tests never touch real
hardware. Real timing is used but kept to tens of milliseconds.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import mido
import pytest

from midiator.midi import MidiDeviceError, MidiSupervisor, read_messages


def field(message: mido.Message, name: str) -> Any:
    """Read a dynamically-defined mido field; see ``midiator.midi._field``."""
    return getattr(message, name, None)


# Short enough to keep the suite quick, long enough to survive a slow machine.
TICK = 0.02
WATCHDOG = 0.15

# A real unplug is reported by the driver within milliseconds, so tests that
# exercise that path use a watchdog no real user would configure. If they pass
# with this, the disconnect was detected properly rather than by timeout.
REALISTIC_WATCHDOG = 10.0


class FakePort:
    """A stand-in for a ``mido`` input port that a test drives by hand.

    Waking works the way a real driver notification does: ``send``, ``unplug``
    and ``close`` all signal the same event, so a reader blocked inside
    ``__iter__`` notices immediately instead of waiting out a poll interval.
    """

    def __init__(self, name: str = "Fake MIDI") -> None:
        self.name = name
        self.sent: list[mido.Message] = []
        self.closed = False
        self._wake = threading.Event()
        self._error: BaseException | None = None
        self._unplugged = False

    def send(self, message: mido.Message) -> None:
        """Queue a message for whoever is iterating this port."""
        self.sent.append(message)
        self._wake.set()

    def unplug(self, error: BaseException | None = None) -> None:
        """End iteration, optionally by raising ``error``."""
        self._error = error
        self._unplugged = True
        self._wake.set()

    def close(self) -> None:
        """Called by the supervisor when it gives up on this port."""
        self.closed = True
        self._unplugged = True
        self._wake.set()

    def __iter__(self):
        index = 0
        while True:
            if index < len(self.sent):
                yield self.sent[index]
                index += 1
                continue
            if self._unplugged:
                if self._error is not None:
                    raise self._error
                return
            self._wake.clear()
            self._wake.wait(1.0)


class StubbornPort(FakePort):
    """A port that accepts a connection and then produces nothing at all.

    This is the case ``mido`` cannot report: the device is gone but the blocking
    iterator simply never returns, so only the watchdog can notice.
    """

    def __iter__(self):
        while not self.closed and not self._unplugged:
            self._wake.clear()
            self._wake.wait(1.0)
        return
        yield  # pragma: no cover - unreachable, keeps this a generator


class FakeDevice:
    """Hands out ports on demand, like Windows does as devices come and go."""

    def __init__(self, ports: list[FakePort] | None = None) -> None:
        self._pending = list(ports or [])
        self.opened: list[FakePort] = []
        self.attempts = 0

    def add(self, port: FakePort) -> None:
        """Make ``port`` the next one this device hands out."""
        self._pending.append(port)

    def open(self) -> FakePort:
        self.attempts += 1
        if not self._pending:
            raise MidiDeviceError("no MIDI input matches 'Fake'. Available inputs:\n  (none)")
        port = self._pending.pop(0)
        self.opened.append(port)
        return port


def make_supervisor(device: FakeDevice, **kwargs: object) -> MidiSupervisor:
    options: dict[str, Any] = {
        "retry_seconds": TICK,
        "watchdog_seconds": WATCHDOG,
        "reconnect": True,
    }
    options.update(kwargs)
    return MidiSupervisor(device.open, **options)  # type: ignore[arg-type]


class Recorder:
    """Collects what the supervisor reported."""

    def __init__(self) -> None:
        self.messages: list[mido.Message] = []
        self.disconnects: list[BaseException | None] = []
        self.waiting: list[MidiDeviceError] = []

    def handle(self, message: mido.Message) -> None:
        self.messages.append(message)

    def on_disconnect(self, error: BaseException | None) -> None:
        self.disconnects.append(error)

    def on_waiting(self, error: MidiDeviceError) -> None:
        self.waiting.append(error)


def run_in_background(supervisor: MidiSupervisor, recorder: Recorder) -> threading.Thread:
    thread = threading.Thread(
        target=supervisor.run,
        kwargs={
            "handle": recorder.handle,
            "on_disconnect": recorder.on_disconnect,
            "on_waiting": recorder.on_waiting,
        },
        daemon=True,
    )
    thread.start()
    return thread


def wait_for(predicate, timeout: float = 5.0) -> bool:
    """Wait for ``predicate`` to become true, polling briefly."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class TestReadMessages:
    def test_yields_connected_messages(self) -> None:
        port = FakePort()
        port.send(mido.Message("note_on", note=60, velocity=100))
        port.unplug()
        events = list(read_messages(port))
        assert events[0][0] is True
        assert events[0][1] is not None and events[0][1].hex() == "90 3C 64"
        assert events[-1] == (False, None, None)

    def test_reports_the_error_that_ended_iteration(self) -> None:
        port = FakePort()
        port.unplug(OSError("device gone"))
        events = list(read_messages(port))
        assert events[-1][0] is False
        assert isinstance(events[-1][2], OSError)


class TestInitialConnection:
    def test_delivers_messages_and_becomes_ready(self) -> None:
        port = FakePort()
        device = FakeDevice([port])
        supervisor = make_supervisor(device)
        recorder = Recorder()
        thread = run_in_background(supervisor, recorder)

        assert supervisor.wait_until_ready(timeout=5.0)
        port.send(mido.Message("note_on", note=64, velocity=100))
        assert wait_for(lambda: len(recorder.messages) == 1)

        supervisor.stop()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert recorder.messages[0].hex() == "90 40 64"

    def test_waits_for_a_device_that_is_not_there_yet(self) -> None:
        """The original complaint: start MIDIator first, plug the keyboard in after."""
        port = FakePort()
        device = FakeDevice([])  # nothing plugged in yet
        supervisor = make_supervisor(device)
        recorder = Recorder()
        thread = run_in_background(supervisor, recorder)

        # No device: it must keep trying rather than exit.
        assert wait_for(lambda: device.attempts >= 3)
        assert thread.is_alive()
        assert not supervisor.wait_until_ready(timeout=0.05)
        assert recorder.waiting, "the user should be told it is waiting"

        # Now plug it in.
        device.add(port)
        assert supervisor.wait_until_ready(timeout=5.0)
        port.send(mido.Message("note_on", note=60, velocity=100))
        assert wait_for(lambda: len(recorder.messages) == 1)

        supervisor.stop()
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    def test_gives_up_after_wait_seconds(self) -> None:
        device = FakeDevice([])
        supervisor = make_supervisor(device, wait_seconds=0.1)
        recorder = Recorder()
        thread = run_in_background(supervisor, recorder)
        thread.join(timeout=5.0)

        assert not thread.is_alive(), "should have returned once the wait budget ran out"
        assert not supervisor.wait_until_ready(timeout=0.05)

    def test_exits_immediately_when_reconnect_is_disabled(self) -> None:
        device = FakeDevice([])
        supervisor = make_supervisor(device, reconnect=False)
        recorder = Recorder()
        with pytest.raises(MidiDeviceError, match="Available inputs"):
            supervisor.run(recorder.handle, on_waiting=recorder.on_waiting)


class TestDisconnect:
    def test_reports_disconnect_and_reconnects(self) -> None:
        first, second = FakePort("First"), FakePort("Second")
        device = FakeDevice([first, second])
        # A long watchdog proves the disconnect came from the port, not a timeout.
        supervisor = make_supervisor(device, watchdog_seconds=REALISTIC_WATCHDOG)
        recorder = Recorder()
        thread = run_in_background(supervisor, recorder)

        assert supervisor.wait_until_ready(timeout=5.0)
        first.send(mido.Message("note_on", note=60, velocity=100))
        assert wait_for(lambda: len(recorder.messages) == 1)

        first.unplug(OSError("device gone"))
        assert wait_for(lambda: len(recorder.disconnects) == 1)
        assert isinstance(recorder.disconnects[0], OSError)

        # The second port must take over and keep delivering.
        assert wait_for(lambda: len(device.opened) == 2)
        second.send(mido.Message("note_on", note=62, velocity=100))
        assert wait_for(lambda: len(recorder.messages) == 2)

        supervisor.stop()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert first.closed

    def test_watchdog_notices_a_silently_dead_port(self) -> None:
        stuck, healthy = StubbornPort("Stuck"), FakePort("Healthy")
        # Queue the message before the port is opened, so it is delivered the
        # moment the healthy port is iterated and cannot race the watchdog.
        healthy.send(mido.Message("note_on", note=67, velocity=100))
        device = FakeDevice([stuck, healthy])
        supervisor = make_supervisor(device)
        recorder = Recorder()
        thread = run_in_background(supervisor, recorder)

        assert supervisor.wait_until_ready(timeout=5.0)
        # The stuck port never yields and never raises, so only the watchdog can
        # notice, and it must do so without any user action.
        assert wait_for(lambda: len(recorder.disconnects) == 1, timeout=5.0)
        assert recorder.disconnects[0] is None
        assert stuck.closed

        assert wait_for(lambda: len(device.opened) == 2)
        assert wait_for(lambda: len(recorder.messages) == 1)
        assert recorder.messages[0].hex() == "90 43 64"

        supervisor.stop()
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    def test_reconnect_disabled_raises_on_disconnect(self) -> None:
        port = FakePort()
        device = FakeDevice([port])
        supervisor = make_supervisor(device, reconnect=False, watchdog_seconds=REALISTIC_WATCHDOG)
        recorder = Recorder()
        captured: list[BaseException] = []

        def run() -> None:
            try:
                supervisor.run(recorder.handle, on_disconnect=recorder.on_disconnect)
            except BaseException as error:
                captured.append(error)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        assert supervisor.wait_until_ready(timeout=5.0)

        port.unplug(OSError("device gone"))
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert captured and isinstance(captured[0], MidiDeviceError)
        assert "reconnect is disabled" in str(captured[0])
        # The caller is still told, so it can release held keys.
        assert len(recorder.disconnects) == 1

    def test_state_carries_across_a_reconnect(self) -> None:
        """A note pair split by a disconnect must still reach the handler."""
        first, second = FakePort("First"), FakePort("Second")
        device = FakeDevice([first, second])
        supervisor = make_supervisor(device)
        recorder = Recorder()
        thread = run_in_background(supervisor, recorder)

        assert supervisor.wait_until_ready(timeout=5.0)
        first.send(mido.Message("note_on", note=60, velocity=100))
        assert wait_for(lambda: len(recorder.messages) == 1)
        first.unplug(OSError("gone"))

        assert wait_for(lambda: len(device.opened) == 2)
        second.send(mido.Message("note_off", note=60, velocity=0))
        assert wait_for(lambda: len(recorder.messages) == 2)
        assert [m.hex() for m in recorder.messages] == ["90 3C 64", "80 3C 00"]

        supervisor.stop()
        thread.join(timeout=5.0)


class TestStop:
    def test_stop_unblocks_a_running_loop(self) -> None:
        port = FakePort()
        device = FakeDevice([port])
        supervisor = make_supervisor(device)
        recorder = Recorder()
        thread = run_in_background(supervisor, recorder)
        assert supervisor.wait_until_ready(timeout=5.0)

        supervisor.stop()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert port.closed

    def test_stop_during_a_device_wait(self) -> None:
        device = FakeDevice([])
        supervisor = make_supervisor(device)
        recorder = Recorder()
        thread = run_in_background(supervisor, recorder)
        assert wait_for(lambda: device.attempts >= 2)

        supervisor.stop()
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    def test_stop_before_run_returns_immediately(self) -> None:
        device = FakeDevice([FakePort()])
        supervisor = make_supervisor(device)
        supervisor.stop()
        supervisor.run(Recorder().handle)
        assert device.attempts == 0

    def test_run_is_safe_to_call_after_stop(self) -> None:
        supervisor = make_supervisor(FakeDevice([FakePort()]))
        supervisor.stop()
        supervisor.stop()
        assert not supervisor.wait_until_ready(timeout=0.05)
