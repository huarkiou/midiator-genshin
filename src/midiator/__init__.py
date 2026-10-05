"""MIDIator: use a MIDI device as a Windows keyboard and mouse.

Originally by ajstensland (https://github.com/ajstensland/MIDIator); this fork
adds a UV-managed package layout, a corrected ``SendInput`` layer, and a
Genshin Impact instrument preset.
"""

from midiator.config import Config, load_config
from midiator.engine import Midiator

__version__ = "0.2.0"
__all__ = ["Config", "Midiator", "__version__", "load_config"]
