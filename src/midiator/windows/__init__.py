"""Windows input backends built on ``SendInput``.

``MouseFlag`` stays unexported on purpose: it is the raw ``dwFlags`` bit field
from Windows, whereas callers and configs work in :class:`MouseButton` and
:class:`Direction`.
"""

from midiator.windows.keyboard import KEYBOARD, Key, WindowsKeySender, lookup_key
from midiator.windows.mouse import Direction, MouseButton, WindowsMouseSender

__all__ = [
    "KEYBOARD",
    "Direction",
    "Key",
    "MouseButton",
    "WindowsKeySender",
    "WindowsMouseSender",
    "lookup_key",
]
