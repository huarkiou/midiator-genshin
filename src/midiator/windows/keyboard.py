"""Windows keyboard: scan/virtual-key tables and SendInput-based key events.

Note names and DirectInput scan codes follow Microsoft's documentation:

* Keyboard scan codes: https://learn.microsoft.com/en-us/windows/win32/inputdev/about-keyboard-input
* ``SendInput``:        https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput

Two send modes are supported, and they are genuinely different:

``"scancode"`` (default)
    Sets ``KEYEVENTF_SCANCODE`` and puts a *scan code* in ``wScan``. This is
    what games that read raw input or DirectInput expect, because the scan code
    carries the physical key position.

``"virtualkey"``
    Puts a *virtual-key code* in ``wVk``. This is what normal Win32
    applications expect. The original MIDIator wrote scan codes into the
    virtual-key field, which is why it was unreliable.

The ``INPUT``, ``KEYBDINPUT``, ``MOUSEINPUT`` and ``HARDWAREINPUT`` structure
definitions, and the ``SendInput`` calls built on them, are adapted from
Nicholas Brochu's work on Serpent.AI:

    https://github.com/SerpentAI/SerpentAI/blob/dev/serpent/input_controllers/native_win32_input_controller.py

Serpent.AI is made available under the MIT License, so here is the original
notice as required:

    MIT License

    Copyright (c) 2017-2020 Serpent.AI

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# --- SendInput constants ---------------------------------------------------

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

_MAPVK_VK_TO_VSC = 0


@dataclass(frozen=True, slots=True)
class Key:
    """A physical key, addressed by both Windows naming schemes."""

    name: str
    vk: int
    scancode: int
    extended: bool = False


def _k(name: str, vk: int, scancode: int, extended: bool = False) -> Key:
    return Key(name, vk, scancode, extended)


#: Every key MIDIator can emit. ``scancode`` is the Set-1 make code that
#: ``SendInput`` wants when ``KEYEVENTF_SCANCODE`` is set.
KEYBOARD: dict[str, Key] = {
    # Row 1: escape and function keys
    "ESCAPE": _k("ESCAPE", 0x1B, 0x01),
    "F1": _k("F1", 0x70, 0x3B),
    "F2": _k("F2", 0x71, 0x3C),
    "F3": _k("F3", 0x72, 0x3D),
    "F4": _k("F4", 0x73, 0x3E),
    "F5": _k("F5", 0x74, 0x3F),
    "F6": _k("F6", 0x75, 0x40),
    "F7": _k("F7", 0x76, 0x41),
    "F8": _k("F8", 0x77, 0x42),
    "F9": _k("F9", 0x78, 0x43),
    "F10": _k("F10", 0x79, 0x44),
    "F11": _k("F11", 0x7A, 0x57),
    "F12": _k("F12", 0x7B, 0x58),
    "SYSRQ": _k("SYSRQ", 0x2C, 0x37, True),
    "SCROLL": _k("SCROLL", 0x91, 0x46, True),
    "PAUSE": _k("PAUSE", 0x13, 0x45, True),
    # Row 2
    "GRAVE": _k("GRAVE", 0xC0, 0x29),
    "ONE": _k("ONE", 0x31, 0x02),
    "TWO": _k("TWO", 0x32, 0x03),
    "THREE": _k("THREE", 0x33, 0x04),
    "FOUR": _k("FOUR", 0x34, 0x05),
    "FIVE": _k("FIVE", 0x35, 0x06),
    "SIX": _k("SIX", 0x36, 0x07),
    "SEVEN": _k("SEVEN", 0x37, 0x08),
    "EIGHT": _k("EIGHT", 0x38, 0x09),
    "NINE": _k("NINE", 0x39, 0x0A),
    "ZERO": _k("ZERO", 0x30, 0x0B),
    "MINUS": _k("MINUS", 0xBD, 0x0C),
    "EQUALS": _k("EQUALS", 0xBB, 0x0D),
    "BACK": _k("BACK", 0x08, 0x0E),
    # Row 3
    "TAB": _k("TAB", 0x09, 0x0F),
    "Q": _k("Q", 0x51, 0x10),
    "W": _k("W", 0x57, 0x11),
    "E": _k("E", 0x45, 0x12),
    "R": _k("R", 0x52, 0x13),
    "T": _k("T", 0x54, 0x14),
    "Y": _k("Y", 0x59, 0x15),
    "U": _k("U", 0x55, 0x16),
    "I": _k("I", 0x49, 0x17),
    "O": _k("O", 0x4F, 0x18),
    "P": _k("P", 0x50, 0x19),
    "LBRACKET": _k("LBRACKET", 0xDB, 0x1A),
    "RBRACKET": _k("RBRACKET", 0xDD, 0x1B),
    "BACKSLASH": _k("BACKSLASH", 0xDC, 0x2B),
    # Row 4
    "CAPITAL": _k("CAPITAL", 0x14, 0x3A),
    "A": _k("A", 0x41, 0x1E),
    "S": _k("S", 0x53, 0x1F),
    "D": _k("D", 0x44, 0x20),
    "F": _k("F", 0x46, 0x21),
    "G": _k("G", 0x47, 0x22),
    "H": _k("H", 0x48, 0x23),
    "J": _k("J", 0x4A, 0x24),
    "K": _k("K", 0x4B, 0x25),
    "L": _k("L", 0x4C, 0x26),
    "SEMICOLON": _k("SEMICOLON", 0xBA, 0x27),
    "APOSTROPHE": _k("APOSTROPHE", 0xDE, 0x28),
    "RETURN": _k("RETURN", 0x0D, 0x1C),
    # Row 5
    "LSHIFT": _k("LSHIFT", 0xA0, 0x2A),
    "Z": _k("Z", 0x5A, 0x2C),
    "X": _k("X", 0x58, 0x2D),
    "C": _k("C", 0x43, 0x2E),
    "V": _k("V", 0x56, 0x2F),
    "B": _k("B", 0x42, 0x30),
    "N": _k("N", 0x4E, 0x31),
    "M": _k("M", 0x4D, 0x32),
    "COMMA": _k("COMMA", 0xBC, 0x33),
    "PERIOD": _k("PERIOD", 0xBE, 0x34),
    "SLASH": _k("SLASH", 0xBF, 0x35),
    "RSHIFT": _k("RSHIFT", 0xA1, 0x36),
    # Row 6 and modifiers
    "LCONTROL": _k("LCONTROL", 0xA2, 0x1D),
    "LWIN": _k("LWIN", 0x5B, 0x5B, True),
    "LMENU": _k("LMENU", 0xA4, 0x38),
    "SPACE": _k("SPACE", 0x20, 0x39),
    "RMENU": _k("RMENU", 0xA5, 0x38, True),
    "RWIN": _k("RWIN", 0x5C, 0x5C, True),
    "APPS": _k("APPS", 0x5D, 0x5D, True),
    "RCONTROL": _k("RCONTROL", 0xA3, 0x1D, True),
    # Numpad
    "NUMLOCK": _k("NUMLOCK", 0x90, 0x45, True),
    "DIVIDE": _k("DIVIDE", 0x6F, 0x35, True),
    "MULTIPLY": _k("MULTIPLY", 0x6A, 0x37),
    "SUBTRACT": _k("SUBTRACT", 0x6D, 0x4A),
    "ADD": _k("ADD", 0x6B, 0x4E),
    "NUMPAD0": _k("NUMPAD0", 0x60, 0x52),
    "NUMPAD1": _k("NUMPAD1", 0x61, 0x4F),
    "NUMPAD2": _k("NUMPAD2", 0x62, 0x50),
    "NUMPAD3": _k("NUMPAD3", 0x63, 0x51),
    "NUMPAD4": _k("NUMPAD4", 0x64, 0x4B),
    "NUMPAD5": _k("NUMPAD5", 0x65, 0x4C),
    "NUMPAD6": _k("NUMPAD6", 0x66, 0x4D),
    "NUMPAD7": _k("NUMPAD7", 0x67, 0x47),
    "NUMPAD8": _k("NUMPAD8", 0x68, 0x48),
    "NUMPAD9": _k("NUMPAD9", 0x69, 0x49),
    "DECIMAL": _k("DECIMAL", 0x6E, 0x53),
    "NUMPADCOMMA": _k("NUMPADCOMMA", 0x6C, 0x7E),
    "NUMPADENTER": _k("NUMPADENTER", 0x0D, 0x1C, True),
    "NUMPADEQUALS": _k("NUMPADEQUALS", 0x92, 0x59),
    # Navigation cluster (all extended)
    "INSERT": _k("INSERT", 0x2D, 0x52, True),
    "DELETE": _k("DELETE", 0x2E, 0x53, True),
    "HOME": _k("HOME", 0x24, 0x47, True),
    "END": _k("END", 0x23, 0x4F, True),
    "PRIOR": _k("PRIOR", 0x21, 0x49, True),
    "NEXT": _k("NEXT", 0x22, 0x51, True),
    "UP": _k("UP", 0x26, 0x48, True),
    "DOWN": _k("DOWN", 0x28, 0x50, True),
    "LEFT": _k("LEFT", 0x25, 0x4B, True),
    "RIGHT": _k("RIGHT", 0x27, 0x4D, True),
    # Media keys (extended, no console scan code)
    "MUTE": _k("MUTE", 0xAD, 0x20, True),
    "VOLUMEDOWN": _k("VOLUMEDOWN", 0xAE, 0x2E, True),
    "VOLUMEUP": _k("VOLUMEUP", 0xAF, 0x30, True),
    "NEXTTRACK": _k("NEXTTRACK", 0xB0, 0x19, True),
    "PREVTRACK": _k("PREVTRACK", 0xB1, 0x10, True),
    "MEDIASTOP": _k("MEDIASTOP", 0xB2, 0x24, True),
    "PLAYPAUSE": _k("PLAYPAUSE", 0xB3, 0x22, True),
    "MAIL": _k("MAIL", 0xB4, 0x6C, True),
    "MEDIASELECT": _k("MEDIASELECT", 0xB6, 0x6D, True),
    "MYCOMPUTER": _k("MYCOMPUTER", 0xB5, 0x6B, True),
    "CALCULATOR": _k("CALCULATOR", 0xB7, 0x21, True),
    "WEBSEARCH": _k("WEBSEARCH", 0xAA, 0x65, True),
    "WEBFAVORITES": _k("WEBFAVORITES", 0xAB, 0x66, True),
    "WEBREFRESH": _k("WEBREFRESH", 0xA8, 0x67, True),
    "WEBSTOP": _k("WEBSTOP", 0xA9, 0x68, True),
    "WEBFORWARD": _k("WEBFORWARD", 0xA7, 0x69, True),
    "WEBBACK": _k("WEBBACK", 0xA6, 0x6A, True),
    "WEBHOME": _k("WEBHOME", 0xAC, 0x32, True),
    "POWER": _k("POWER", 0x5E, 0x5E, True),
    "SLEEP": _k("SLEEP", 0x5F, 0x5F, True),
    "WAKE": _k("WAKE", 0xE3, 0x63, True),
}

#: Optional aliases so configs can read naturally.
ALIASES: dict[str, str] = {
    "ESC": "ESCAPE",
    "ENTER": "RETURN",
    "CTRL": "LCONTROL",
    "CONTROL": "LCONTROL",
    "SHIFT": "LSHIFT",
    "ALT": "LMENU",
    "SPACEBAR": "SPACE",
}


def normalise_key_name(name: str) -> str:
    """Upper-case ``name`` and strip an optional ``KEY_`` prefix."""
    cleaned = name.strip().upper()
    if cleaned.startswith("KEY_"):
        cleaned = cleaned[4:]
    return ALIASES.get(cleaned, cleaned)


def lookup_key(name: str) -> Key:
    """Return the :class:`Key` for ``name``, raising :class:`KeyError` if unknown."""
    normalised = normalise_key_name(name)
    try:
        return KEYBOARD[normalised]
    except KeyError:
        raise KeyError(f"unknown key {name!r}; see midiator.windows.keyboard.KEYBOARD") from None


_windll: Any = getattr(ctypes, "windll", None)
_user32: Any = _windll.user32 if _windll is not None else None


def _windows_scancode(vk: int) -> int:
    """Ask Windows which scan code it maps ``vk`` to (0 when unmapped)."""
    if _user32 is None:
        return 0
    return int(_user32.MapVirtualKeyW(vk, _MAPVK_VK_TO_VSC)) & 0xFF


class KeyBdInput(ctypes.Structure):
    """Windows ``KEYBDINPUT``."""

    _fields_ = (
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class HardwareInput(ctypes.Structure):
    """Windows ``HARDWAREINPUT`` (unused, required by the union)."""

    _fields_ = (
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    )


class MouseInput(ctypes.Structure):
    """Windows ``MOUSEINPUT``."""

    _fields_ = (
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class _InputUnion(ctypes.Union):
    _fields_ = (("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput))


class Input(ctypes.Structure):
    """Windows ``INPUT``."""

    _fields_ = (("type", ctypes.c_ulong), ("ii", _InputUnion))


class WindowsKeySender:
    """Sends key press/release events through ``SendInput``.

    :param mode: ``"scancode"`` (default) or ``"virtualkey"``.
    :param dry_run: Log instead of calling ``SendInput``. Useful for testing.
    """

    def __init__(self, mode: str = "scancode", dry_run: bool = False) -> None:
        if mode not in ("scancode", "virtualkey"):
            raise ValueError(f"mode must be 'scancode' or 'virtualkey', got {mode!r}")
        self.mode = mode
        self.dry_run = dry_run
        self._fallback_warned: set[str] = set()

    def _resolve(self, key: Key) -> tuple[int, int, int]:
        """Return ``(wVk, wScan, extra_flags)`` for ``key`` in the active mode."""
        if self.mode == "virtualkey":
            return key.vk, 0, 0

        flags = KEYEVENTF_SCANCODE
        if key.extended:
            flags |= KEYEVENTF_EXTENDEDKEY

        actual = _windows_scancode(key.vk)
        if actual and actual != key.scancode and key.name not in self._fallback_warned:
            self._fallback_warned.add(key.name)
            logger.warning(
                "Scan code for %s is %#04x here but the table says %#04x; sending that key as a virtual key instead.",
                key.name,
                actual,
                key.scancode,
            )
            return key.vk, 0, 0
        return 0, key.scancode, flags

    def send(self, key: Key, pressed: bool) -> None:
        """Press or release ``key``."""
        w_vk, w_scan, flags = self._resolve(key)
        if not pressed:
            flags |= KEYEVENTF_KEYUP
        if self.dry_run:
            logger.info(
                "[dry-run] %s %s (vk=%#04x scan=%#04x flags=%#06x)",
                "press" if pressed else "release",
                key.name,
                w_vk,
                w_scan,
                flags,
            )
            return

        extra = ctypes.c_ulong(0)
        union = _InputUnion()
        union.ki = KeyBdInput(w_vk, w_scan, flags, 0, ctypes.pointer(extra))
        event = Input(INPUT_KEYBOARD, union)
        ctypes.windll.user32.SendInput(1, ctypes.pointer(event), ctypes.sizeof(event))
