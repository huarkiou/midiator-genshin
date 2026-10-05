"""Windows mouse: ``MOUSEINPUT`` flags, movement and button events."""

from __future__ import annotations

import ctypes
import logging
from enum import Enum, IntEnum
from typing import Any

from midiator.windows.keyboard import Input, MouseInput, _InputUnion

logger = logging.getLogger(__name__)

INPUT_MOUSE = 0


class MouseFlag(IntEnum):
    """Values for ``dwFlags`` in Windows ``MOUSEINPUT``."""

    MOVE = 0x0001
    LEFT_DOWN = 0x0002
    LEFT_UP = 0x0004
    RIGHT_DOWN = 0x0008
    RIGHT_UP = 0x0010
    MIDDLE_DOWN = 0x0020
    MIDDLE_UP = 0x0040
    X_DOWN = 0x0080
    X_UP = 0x0100
    WHEEL = 0x0800


class Direction(tuple, Enum):
    """Mouse movement directions as ``(dx, dy)`` offsets, screen coordinates.

    Note the mixin is :class:`Enum`, not :class:`IntEnum`: combining ``tuple``
    with ``IntEnum`` raises ``TypeError: too many data types``. Each member *is*
    a ``(dx, dy)`` tuple, so it unpacks directly.
    """

    LEFT = (-1, 0)
    RIGHT = (1, 0)
    UP = (0, -1)
    DOWN = (0, 1)
    STAY = (0, 0)

    @property
    def vector(self) -> tuple[int, int]:
        """The ``(dx, dy)`` pair, typed so callers do not need casts."""
        dx, dy = self.value
        return int(dx), int(dy)


class WindowsMouseSender:
    """Sends mouse movement and button events through ``SendInput``."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def _send(self, dx: int, dy: int, flags: int) -> None:
        if self.dry_run:
            logger.info("[dry-run] mouse dx=%d dy=%d flags=%#06x", dx, dy, flags)
            return
        extra = ctypes.c_ulong(0)
        union = _InputUnion()
        union.mi = MouseInput(dx, dy, 0, flags, 0, ctypes.pointer(extra))
        event = Input(INPUT_MOUSE, union)
        user32: Any = ctypes.windll.user32
        user32.SendInput(1, ctypes.pointer(event), ctypes.sizeof(event))

    def move(self, dx: int, dy: int) -> None:
        """Move the cursor by ``(dx, dy)`` relative to its current position."""
        if dx == 0 and dy == 0:
            return
        self._send(dx, dy, MouseFlag.MOVE)

    def button(self, flag: MouseFlag) -> None:
        """Send a single button event, e.g. :attr:`MouseFlag.LEFT_DOWN`."""
        self._send(0, 0, int(flag))
