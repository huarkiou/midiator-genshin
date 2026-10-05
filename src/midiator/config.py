"""Configuration loading for MIDIator.

Configuration lives in ``midiator.toml`` beside the project root. Everything is
optional: a missing file, a missing table, or a missing key all fall back to the
defaults below.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    # ``dataclasses.fields`` is typed against typeshed's DataclassInstance;
    # importing it at run time would add a typing_extensions dependency.
    from _typeshed import DataclassInstance

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "midiator.toml"


@dataclass(slots=True)
class MidiSettings:
    """Which MIDI device and channel to listen to."""

    device: str | None = None
    """Part of the MIDI input name. ``None`` picks the first device found."""
    substring: bool = True
    """Match ``device`` anywhere in the port name, or require an exact match."""
    channel: int | None = 0
    """MIDI channel to listen on, ``None`` for every channel."""


@dataclass(slots=True)
class WindowSettings:
    """How translated input is delivered to Windows."""

    key_mode: str = "scancode"
    """``"scancode"`` for games and raw-input consumers, ``"virtualkey"`` otherwise."""
    mouse_sensitivity: int = 4
    """Pixels of mouse travel per tick of a movement bind."""
    mouse_refresh: float = 0.005
    """Seconds between mouse movement updates."""


@dataclass(slots=True)
class GenshinSettings:
    """Settings for the built-in Genshin Impact instrument preset."""

    transpose: int = 0
    """Semitones to shift every note by before mapping, ``-48`` to ``48``."""
    accidental: str = "snap"
    """``"snap"`` black keys onto a neighbour, or ``"skip"`` them."""
    out_of_range: str = "fold"
    """``"fold"`` out-of-range notes by octaves, or ``"skip"`` them."""
    auto_transpose: bool = False
    """Measure the first few notes and adjust ``transpose`` automatically."""


@dataclass(slots=True)
class BindSettings:
    """Explicit MIDI-to-key/mouse binds used by the generic preset."""

    keys: dict[int, str] = field(default_factory=dict)
    mouse_buttons: dict[int, str] = field(default_factory=dict)
    mouse_movement: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class Config:
    """The whole configuration."""

    preset: str = "genshin"
    """``"genshin"`` for the instrument layout, ``"generic"`` for free binds."""
    verbose: bool = True
    midi: MidiSettings = field(default_factory=MidiSettings)
    windows: WindowSettings = field(default_factory=WindowSettings)
    genshin: GenshinSettings = field(default_factory=GenshinSettings)
    binds: BindSettings = field(default_factory=BindSettings)
    path: Path | None = None
    """Where the configuration was read from, if anywhere."""


def _build(cls: type[Any], data: Any, where: str) -> Any:
    """Instantiate the dataclass ``cls`` from the ``data`` table."""
    if not isinstance(data, dict):
        logger.warning("[%s] must be a table; ignoring", where)
        return cls()
    known = {f.name for f in fields(cast("type[DataclassInstance]", cls))}
    unknown = sorted(set(data) - known)
    if unknown:
        logger.warning("Ignoring unknown key(s) in [%s]: %s", where, ", ".join(unknown))
    return cls(**{key: value for key, value in data.items() if key in known})


def _bind_table(data: Any, name: str) -> dict[int, str]:
    """Convert ``{"C4" or 60 = "Z"}`` into ``{60: "Z"}``."""
    if not isinstance(data, dict):
        logger.warning("[binds].%s must be a table; ignoring", name)
        return {}
    converted: dict[int, str] = {}
    for key, value in data.items():
        try:
            note = int(key)
        except (TypeError, ValueError):
            logger.warning("[binds].%s: %r is not a MIDI note number; ignoring", name, key)
            continue
        if not 0 <= note <= 127:
            logger.warning("[binds].%s: MIDI note %d is out of range 0-127; ignoring", name, note)
            continue
        converted[note] = str(value)
    return converted


def load_config(path: str | Path | None = None) -> Config:
    """Read configuration from ``path``, or search for ``midiator.toml``.

    Search order is the current directory, then the project root (the parent of
    the ``src`` directory this package lives in). Returns defaults when no file
    is found.

    :raises ValueError: If the file exists but is not valid TOML.
    """
    resolved = _find_config(path)
    if resolved is None:
        logger.debug("No %s found; using defaults", DEFAULT_CONFIG_NAME)
        return Config()

    try:
        with resolved.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{resolved} is not valid TOML: {error}") from error

    config = Config(path=resolved)
    if not isinstance(raw, dict):
        return config

    config.preset = str(raw.get("preset", config.preset))
    config.verbose = bool(raw.get("verbose", config.verbose))

    if "midi" in raw:
        config.midi = _build(MidiSettings, raw["midi"], "midi")
    if "windows" in raw:
        config.windows = _build(WindowSettings, raw["windows"], "windows")
    if "genshin" in raw:
        config.genshin = _build(GenshinSettings, raw["genshin"], "genshin")
    if "binds" in raw:
        table = raw["binds"]
        if isinstance(table, dict):
            config.binds = BindSettings(
                keys=_bind_table(table.get("keys"), "keys"),
                mouse_buttons=_bind_table(table.get("mouse_buttons"), "mouse_buttons"),
                mouse_movement=_bind_table(table.get("mouse_movement"), "mouse_movement"),
            )

    _validate(config, resolved)
    return config


def _find_config(path: str | Path | None) -> Path | None:
    if path is not None:
        candidate = Path(path)
        if not candidate.is_file():
            raise FileNotFoundError(f"configuration file not found: {candidate}")
        return candidate

    candidates = [Path.cwd() / DEFAULT_CONFIG_NAME]
    # src/midiator/config.py -> src/midiator -> src -> project root
    project_root = Path(__file__).resolve().parents[2]
    candidates.append(project_root / DEFAULT_CONFIG_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _validate(config: Config, path: Path) -> None:
    if config.windows.key_mode not in ("scancode", "virtualkey"):
        raise ValueError(f"{path}: [windows].key_mode must be 'scancode' or 'virtualkey'")
    if config.genshin.accidental not in ("snap", "skip"):
        raise ValueError(f"{path}: [genshin].accidental must be 'snap' or 'skip'")
    if config.genshin.out_of_range not in ("fold", "skip"):
        raise ValueError(f"{path}: [genshin].out_of_range must be 'fold' or 'skip'")
    if not -48 <= config.genshin.transpose <= 48:
        raise ValueError(f"{path}: [genshin].transpose must be between -48 and 48")
    if config.midi.channel is not None and not 0 <= config.midi.channel <= 15:
        raise ValueError(f"{path}: [midi].channel must be between 0 and 15, or omitted for all channels")
    if config.midi.substring is False and not config.midi.device:
        raise ValueError(f"{path}: [midi].substring=false needs an exact [midi].device name")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(value)


def render_default_config() -> str:
    """Render the default configuration as TOML, for ``midiator --init``."""
    defaults = Config()
    lines = [
        "# MIDIator configuration.",
        "# Every key is optional; delete a line to fall back to its default.",
        "",
        "# 'genshin' maps notes onto a Genshin Impact instrument.",
        "# 'generic' uses the explicit [binds] tables further down.",
        f"preset = {_toml_value(defaults.preset)}",
        "",
        "# Print what every note turns into. Handy while setting up.",
        f"verbose = {_toml_value(defaults.verbose)}",
        "",
        "[midi]",
        "# Part of your MIDI device's name, case-insensitive.",
        '# device = "Casio"',
        "# Set to false to require the name to match exactly.",
        f"substring = {_toml_value(defaults.midi.substring)}",
        "# MIDI channel 0-15, or comment out to listen on all channels.",
        f"channel = {_toml_value(defaults.midi.channel)}",
        "",
        "[windows]",
        "# 'scancode' suits games; 'virtualkey' suits normal applications.",
        f"key_mode = {_toml_value(defaults.windows.key_mode)}",
        f"mouse_sensitivity = {_toml_value(defaults.windows.mouse_sensitivity)}",
        f"mouse_refresh = {_toml_value(defaults.windows.mouse_refresh)}",
        "",
        "[genshin]",
        "# Semitones to shift every note by, -48 to 48.",
        f"transpose = {_toml_value(defaults.genshin.transpose)}",
        "# 'snap' moves black keys to a neighbouring white key; 'skip' drops them.",
        f"accidental = {_toml_value(defaults.genshin.accidental)}",
        "# 'fold' shifts notes outside C3-B5 by octaves; 'skip' drops them.",
        f"out_of_range = {_toml_value(defaults.genshin.out_of_range)}",
        "# Transpose automatically from the first notes you play.",
        f"auto_transpose = {_toml_value(defaults.genshin.auto_transpose)}",
        "",
        "# Only used when preset = 'generic'. Notes are MIDI numbers.",
        "# [binds.keys]",
        '# 42 = "W"      # F#3 -> W',
        '# 50 = "SPACE"  # D4  -> space',
        "# [binds.mouse_buttons]",
        '# 48 = "LEFT_DOWN"   # C4 -> left click',
        "# [binds.mouse_movement]",
        '# 52 = "LEFT"        # E4 -> move the cursor left while held',
        "",
    ]
    return "\n".join(lines)


def write_default_config(path: str | Path) -> Path:
    """Write the default configuration to ``path`` without overwriting."""
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"{destination} already exists")
    destination.write_text(render_default_config(), encoding="utf-8")
    return destination
