"""Windows input backends built on ``SendInput``."""

from midiator.windows.keyboard import KEYBOARD, Key, WindowsKeySender, lookup_key
from midiator.windows.mouse import Direction, MouseFlag, WindowsMouseSender

__all__ = [
    "KEYBOARD",
    "Direction",
    "Key",
    "MouseFlag",
    "WindowsKeySender",
    "WindowsMouseSender",
    "lookup_key",
]
