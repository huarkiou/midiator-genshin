"""MIDI device discovery and message listening, built on ``mido``/``rtmidi``."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

import mido

logger = logging.getLogger(__name__)


class MidiDeviceError(RuntimeError):
    """Raised when no usable MIDI input device can be opened."""


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
