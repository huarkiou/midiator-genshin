"""Shared test doubles: senders that record instead of touching the desktop."""

from __future__ import annotations

import pytest

from midiator.windows.keyboard import Key
from midiator.windows.mouse import MouseFlag


class RecordingKeySender:
    """Collects ``(key_name, pressed)`` pairs."""

    def __init__(self) -> None:
        self.events: list[tuple[str, bool]] = []

    def send(self, key: Key, pressed: bool) -> None:
        self.events.append((key.name, pressed))

    @property
    def presses(self) -> list[str]:
        return [name for name, pressed in self.events if pressed]

    @property
    def releases(self) -> list[str]:
        return [name for name, pressed in self.events if not pressed]

    def clear(self) -> None:
        self.events.clear()


class RecordingMouseSender:
    """Collects movement offsets and button flags."""

    def __init__(self) -> None:
        self.moves: list[tuple[int, int]] = []
        self.buttons: list[MouseFlag] = []

    def move(self, dx: int, dy: int) -> None:
        self.moves.append((dx, dy))

    def button(self, flag: MouseFlag) -> None:
        self.buttons.append(flag)

    def clear(self) -> None:
        self.moves.clear()
        self.buttons.clear()


@pytest.fixture
def key_sender() -> RecordingKeySender:
    return RecordingKeySender()


@pytest.fixture
def mouse_sender() -> RecordingMouseSender:
    return RecordingMouseSender()
