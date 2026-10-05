"""MIDI device discovery, message listening and hot-plug supervision."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol, runtime_checkable

import mido

logger = logging.getLogger(__name__)


class MidiDeviceError(RuntimeError):
    """Raised when no usable MIDI input device can be opened."""


class DeviceLost(RuntimeError):
    """Raised by a port's iterator when the device goes away mid-session."""


#: How often the main loop checks for shutdown while waiting for messages. Keeps
#: Ctrl+C responsive without turning the wait into a busy loop.
STOP_POLL_SECONDS = 0.25


@runtime_checkable
class MidiInput(Protocol):
    """The part of a ``mido`` input port this package actually uses.

    ``mido`` and ``rtmidi`` ship no type information for their port helpers
    (``mido.ports.get_input_names``, ``mido.ports.open_input``), so those are
    reached through ``_ports()`` and the resulting object is described here.
    Backend failures arrive as :class:`OSError`, because mido's ``rtmidi``
    backend wraps ``rtmidi.SystemError`` before re-raising it.
    """

    name: str

    def __iter__(self) -> Iterator[mido.Message]:
        """Iterate over incoming messages, blocking between them."""

    def close(self) -> None:
        """Close the port."""


def _port_helper(name: str) -> Any:
    """Reach a ``mido`` port helper that its ``__all__`` forgets to export.

    ``get_input_names`` and ``open_input`` are public API and live on the
    ``mido`` namespace, but mido ships no type stubs for them, so they are read
    dynamically rather than imported.
    """
    helper = getattr(mido, name, None)
    if helper is None:
        raise MidiDeviceError(f"the installed mido has no {name}; is mido>=1.3 installed?")
    return helper


def list_input_names() -> list[str]:
    """Return the names of every MIDI input port Windows currently reports."""
    try:
        return list(_port_helper("get_input_names")())
    except OSError as error:  # pragma: no cover - needs host MIDI stack
        raise MidiDeviceError(f"could not enumerate MIDI inputs: {error}") from error


def find_device(identifier: str, substring: bool = True) -> str:
    """Return the name of the first input port matching ``identifier``.

    :param identifier: Device name to look for. Matching is case-insensitive.
    :param substring: Match anywhere in the name (default), otherwise require an
        exact match.
    :raises MidiDeviceError: If nothing matches, listing what is available.
    """
    names = list_input_names()
    needle = identifier.lower()
    for name in names:
        haystack = name.lower()
        if (needle in haystack) if substring else (needle == haystack):
            return name

    available = "\n  ".join(names) if names else "(no MIDI inputs found)"
    raise MidiDeviceError(f"no MIDI input matches {identifier!r}. Available inputs:\n  {available}")


def open_input(identifier: str | None = None, substring: bool = True) -> MidiInput:
    """Open a MIDI input port by name, or the first available one when ``None``.

    :raises MidiDeviceError: If the device is missing or already claimed by
        another process.
    """
    if identifier is None:
        names = list_input_names()
        if not names:
            raise MidiDeviceError("no MIDI inputs are connected")
        name = names[0]
        logger.info("No device configured; using %r", name)
    else:
        name = find_device(identifier, substring=substring)

    try:
        port = _port_helper("open_input")(name)
    except OSError as error:
        raise MidiDeviceError(f"could not open MIDI input {name!r}: {error}") from error
    return port


def iter_messages(port: MidiInput) -> Iterator[mido.Message]:
    """Yield MIDI messages from ``port`` until the iterator is closed.

    ``mido``'s port iterator blocks between messages and wakes on the port's
    internal callback thread, so this does not spin.
    """
    yield from port


def read_messages(port: MidiInput) -> Iterator[tuple[bool, mido.Message | None, BaseException | None]]:
    """Yield ``(connected, message, error)`` until ``port`` stops producing.

    This is the testable core of :class:`MidiSupervisor`'s reader thread. The
    final item always reports a disconnect, either with the exception the port
    raised or with ``None`` when the port simply ended.
    """
    try:
        for message in port:
            yield True, message, None
    except GeneratorExit:  # pragma: no cover - only on interpreter shutdown
        raise
    except BaseException as error:
        yield False, None, error
        return
    yield False, None, None


class MidiSupervisor:
    """Keeps a MIDI input open across hot-plugging.

    A MIDI device can be missing when MIDIator starts, and can disappear while
    it runs. This class waits for the device to appear, and reopens it after a
    disconnect, calling ``on_disconnect`` so the caller can release any keys it
    is holding.

    Ports are read on a background thread that feeds a queue, because ``mido``'s
    blocking iterator cannot be interrupted: a device that is unplugged may go
    silent without raising, and a plain ``for message in port`` would hang
    forever. The queue gives the main loop a timeout, which doubles as a
    watchdog for that silent case.
    """

    def __init__(
        self,
        open_port: Callable[[], MidiInput],
        *,
        retry_seconds: float = 2.0,
        watchdog_seconds: float = 5.0,
        wait_seconds: float | None = None,
        reconnect: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.open_port = open_port
        self.retry_seconds = retry_seconds
        self.watchdog_seconds = watchdog_seconds
        self.wait_seconds = wait_seconds
        self.reconnect = reconnect
        self._clock = clock

        self._stop = threading.Event()
        self._ready = threading.Event()
        self._generation = 0
        self._last_error: BaseException | None = None
        self._current_port: MidiInput | None = None
        self._queue: queue.Queue[tuple[int, bool, mido.Message | None, BaseException | None]] = queue.Queue(
            maxsize=1024
        )

    # -- lifecycle --------------------------------------------------------

    def stop(self) -> None:
        """Ask :meth:`run` to finish after the current iteration."""
        self._stop.set()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Block until a device has been opened. Returns ``False`` on timeout."""
        return self._ready.wait(timeout)

    # -- ports ------------------------------------------------------------

    def _connect(
        self,
        on_waiting: Callable[[MidiDeviceError], None] | None,
    ) -> MidiInput | None:
        """Open the port, retrying until the device appears or the wait is over.

        Returns ``None`` when MIDIator is stopping or ``wait_seconds`` ran out
        without a device, otherwise a connected port.

        :raises MidiDeviceError: When ``reconnect`` is disabled and no device is
            available.
        """
        deadline = None if self.wait_seconds is None else self._clock() + self.wait_seconds
        last_error: MidiDeviceError | None = None

        while not self._stop.is_set():
            try:
                port = self.open_port()
            except MidiDeviceError as error:
                last_error = error
                if on_waiting is not None:
                    on_waiting(error)
                    on_waiting = None
                if not self.reconnect:
                    raise
                if deadline is not None and self._clock() >= deadline:
                    return None
                self._stop.wait(self.retry_seconds)
                continue

            self._ready.set()
            return port

        # Stopping: not an error, just nobody left to listen.
        logger.debug("Gave up connecting because MIDIator is stopping (%s)", last_error)
        return None

    def _close_port(self, port: MidiInput | None) -> None:
        if port is None:
            return
        try:
            port.close()
        except Exception:
            logger.debug("Ignoring failure while closing the MIDI port", exc_info=True)

    def _drain(self) -> None:
        """Drop anything the old port left behind."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _start_reader(self, port: MidiInput) -> None:
        generation = self._generation

        def read() -> None:
            for connected, message, error in read_messages(port):
                if generation != self._generation:
                    return  # a newer port has taken over; stop touching the queue
                try:
                    self._queue.put((generation, connected, message, error), timeout=1.0)
                except queue.Full:  # pragma: no cover - consumer is far too slow
                    logger.warning("MIDI queue full; dropping a message")

        threading.Thread(target=read, name="midiator-midi-reader", daemon=True).start()

    # -- main loop --------------------------------------------------------

    def run(
        self,
        handle: Callable[[mido.Message], None],
        on_disconnect: Callable[[BaseException | None], None] | None = None,
        on_waiting: Callable[[MidiDeviceError], None] | None = None,
    ) -> None:
        """Read MIDI messages until :meth:`stop` is called or the interface closes.

        :param handle: Called for every message that arrives.
        :param on_disconnect: Called with the error (or ``None``) when the device
            is lost, before MIDIator starts looking for it again.
        :param on_waiting: Called once per connection attempt while no device is
            available.
        """
        while not self._stop.is_set():
            port = self._connect(on_waiting)
            if port is None:
                return

            name = getattr(port, "name", "MIDI input")
            self._generation += 1
            generation = self._generation
            self._current_port = port

            try:
                self._start_reader(port)
                self._read_until_lost(generation, name, handle)
            except KeyboardInterrupt:
                self._close_port(port)
                raise
            finally:
                self._current_port = None
                self._close_port(port)

            if self._stop.is_set():
                return

            if not self.reconnect:
                # Still tell the caller, so it can release anything it holds.
                if on_disconnect is not None:
                    on_disconnect(self._last_error)
                raise MidiDeviceError("MIDI device disconnected and reconnect is disabled")

            self._drain()
            if on_disconnect is not None:
                on_disconnect(self._last_error)
            self._last_error = None

            self._stop.wait(self.retry_seconds)

    def _read_until_lost(
        self,
        generation: int,
        name: str,
        handle: Callable[[mido.Message], None],
    ) -> None:
        """Consume the queue until the device looks lost or MIDIator is stopping."""
        # Wake often enough that stop() is honoured promptly, while only treating
        # a full watchdog_seconds of silence as suspicious.
        tick = min(self.watchdog_seconds, STOP_POLL_SECONDS)
        silent_for = 0.0

        while not self._stop.is_set():
            try:
                item_generation, connected, message, error = self._queue.get(timeout=tick)
            except queue.Empty:
                silent_for += tick
                if silent_for < self.watchdog_seconds:
                    continue
                # Silence can mean the device was unplugged without WinMM saying
                # so. Closing the port wakes the reader, which then reports the
                # error, so ask it what happened before assuming anything.
                logger.info(
                    "No MIDI messages from %r for %.1fs; checking whether the device is still there.",
                    name,
                    self.watchdog_seconds,
                )
                self._close_port(self._current_port)
                self._last_error = self._collect_reader_error(generation)
                return

            silent_for = 0.0
            if item_generation != generation:
                continue
            if connected and message is not None:
                handle(message)
                continue

            self._last_error = error
            if error is not None:
                logger.warning("MIDI device %r was lost: %s", name, error)
            else:
                logger.info("MIDI device %r stopped producing messages.", name)
            return

    def _collect_reader_error(self, generation: int) -> BaseException | None:
        """Give the reader thread a moment to report why the port went quiet."""
        deadline = self._clock() + 0.25
        while self._clock() < deadline:
            try:
                item_generation, connected, _message, error = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if item_generation == generation and not connected:
                return error
        return None


def _field(message: mido.Message, name: str) -> Any:
    """Read a dynamically-defined field from a ``mido`` message.

    ``mido`` builds its message classes at import time, so ``type``, ``note``,
    ``velocity`` and friends are not declared anywhere a type checker can see.
    """
    return getattr(message, name, None)


class HeldNotes:
    """Tracks which pitches are currently sounding.

    MIDI keyboards send ``note_on`` with velocity 0 instead of ``note_off``, and
    can repeat ``note_on`` for a key that is already down. Both cases are
    normalised here so the caller only sees real transitions.
    """

    def __init__(self, channel: int | None = None) -> None:
        self.channel = channel
        self._held: set[int] = set()

    def __contains__(self, note: int) -> bool:
        return note in self._held

    def __len__(self) -> int:
        return len(self._held)

    @property
    def held(self) -> frozenset[int]:
        """The pitches currently down."""
        return frozenset(self._held)

    def accepts(self, message: mido.Message) -> bool:
        """Whether ``message`` belongs to the configured MIDI channel."""
        if self.channel is None:
            return True
        return getattr(message, "channel", None) == self.channel

    def update(self, message: mido.Message) -> tuple[int, bool] | None:
        """Record ``message`` and report the transition it represents.

        :return: ``(note, pressed)`` when the message changes the held state,
            otherwise ``None``.
        """
        kind = _field(message, "type")
        if kind not in ("note_on", "note_off"):
            return None
        if not self.accepts(message):
            return None

        note = int(_field(message, "note") or 0)
        pressed = kind == "note_on" and int(_field(message, "velocity") or 0) > 0

        if pressed:
            if note in self._held:
                return None
            self._held.add(note)
            return note, True

        if note not in self._held:
            return None
        self._held.discard(note)
        return note, False

    def all_off(self) -> tuple[int, ...]:
        """Forget every held note and return the pitches that were down."""
        released = tuple(sorted(self._held))
        self._held.clear()
        return released
