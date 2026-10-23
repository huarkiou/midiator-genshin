"""The note-driven engine: turns MIDI transitions into Windows input events."""

from __future__ import annotations

import logging
import threading
from collections import Counter
from dataclasses import dataclass
from math import sqrt
from typing import Protocol, runtime_checkable

from midiator.config import Config
from midiator.midi import HeldNotes
from midiator.notes import GenshinInstrumentMapping, NotePlan, pitch_name, suggest_transpose
from midiator.windows.keyboard import Key, lookup_key
from midiator.windows.mouse import Direction, MouseButton

logger = logging.getLogger(__name__)

#: Notes to observe before ``auto_transpose`` locks in a suggestion.
AUTO_TRANSPOSE_SAMPLE = 12


@runtime_checkable
class KeySender(Protocol):
    """Something that can press and release a keyboard key."""

    def send(self, key: Key, pressed: bool) -> None:
        """Press ``key`` when ``pressed``, otherwise release it."""


@runtime_checkable
class MouseSender(Protocol):
    """Something that can move the cursor and click buttons."""

    def move(self, dx: int, dy: int) -> None:
        """Move the cursor by ``(dx, dy)``."""

    def button(self, button: MouseButton, pressed: bool) -> None:
        """Press or release ``button``."""


#: Suffixes upstream MIDIator used to name press/release flags separately.
_FLAG_SUFFIXES = ("_DOWN", "_UP", "_CLICK", "_RELEASE", "_PRESS", "_RELEASED")


def _unknown_button_message(name: str) -> str:
    """Explain a rejected ``[binds.mouse_buttons]`` value.

    Upstream MIDIator configured the press and release flags separately, so old
    configs name a flag like ``LEFT_DOWN`` or ``LEFT_CLICK``. Point at the button
    name that replaces it rather than leaving the user to guess which half is
    wrong.
    """
    known = ", ".join(b.name for b in MouseButton)
    cleaned = name.strip().upper()
    for suffix in _FLAG_SUFFIXES:
        if cleaned.endswith(suffix):
            button = cleaned[: -len(suffix)]
            if button in MouseButton.__members__:
                return (
                    f"{name} is a press/release flag. Use {button!r} instead: MIDIator presses on "
                    f"note-on and releases on note-off, so only the button name belongs here."
                )
    return f"unknown mouse button {name!r}; use one of: {known}"


@dataclass(frozen=True, slots=True)
class Target:
    """What one note does when it goes down."""

    kind: str
    """``"key"``, ``"button"``, ``"move"`` or ``"unbound"``."""
    key: Key | None = None
    button: MouseButton | None = None
    direction: Direction | None = None
    plan: NotePlan | None = None

    @property
    def label(self) -> str:
        """A short name for the target, for logs and previews."""
        if self.kind == "key" and self.key is not None:
            return f"Key {self.key.name}"
        if self.kind == "button" and self.button is not None:
            return f"Mouse {self.button.name}"
        if self.kind == "move" and self.direction is not None:
            return f"Mouse {self.direction.name}"
        return "not bound"


class Midiator:
    """Translates MIDI notes into Windows keyboard and mouse input.

    The engine is deliberately free of Windows calls: senders are injected, so
    the whole translation layer can be tested without touching the real desktop.
    """

    def __init__(
        self,
        config: Config,
        keys: KeySender,
        mouse: MouseSender,
    ) -> None:
        self.config = config
        self.keys = keys
        self.mouse = mouse
        self.notes = HeldNotes(channel=config.midi.channel)

        self.genshin = (
            GenshinInstrumentMapping(
                transpose=config.genshin.transpose,
                accidental=config.genshin.accidental,
                out_of_range=config.genshin.out_of_range,
            )
            if config.preset == "genshin"
            else None
        )

        self._key_binds = self._build_key_binds()
        self._button_binds = self._build_button_binds()
        self._move_binds = self._build_move_binds()

        #: MIDI note -> the key that note currently holds down.
        self._held_keys: dict[int, Key] = {}
        #: MIDI note -> the mouse button that note currently holds down.
        self._held_buttons: dict[int, MouseButton] = {}
        #: How many notes are pushing the cursor in each direction.
        self._move_counts: Counter[Direction] = Counter()
        self._move_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._auto_notes: list[int] = []
        self._auto_transpose_done = False
        self.stats: Counter[str] = Counter()

    # -- setup ------------------------------------------------------------

    def _build_key_binds(self) -> dict[int, Key]:
        if self.genshin is not None:
            return {}
        return {note: lookup_key(name) for note, name in self.config.binds.keys.items()}

    def _build_button_binds(self) -> dict[int, MouseButton]:
        if self.genshin is not None:
            return {}
        binds: dict[int, MouseButton] = {}
        for note, name in self.config.binds.mouse_buttons.items():
            try:
                binds[note] = MouseButton[name.strip().upper()]
            except KeyError:
                raise KeyError(_unknown_button_message(name)) from None
        return binds

    def _build_move_binds(self) -> dict[int, Direction]:
        if self.genshin is not None:
            return {}
        binds: dict[int, Direction] = {}
        for note, name in self.config.binds.mouse_movement.items():
            try:
                binds[note] = Direction[name.strip().upper()]
            except KeyError:
                raise KeyError(f"unknown mouse direction {name!r}; try one of {[d.name for d in Direction]}") from None
        return binds

    # -- logging ----------------------------------------------------------

    def log(self, message: str, *args: object) -> None:
        """Log a setup or mapping message when ``verbose`` is on."""
        if self.config.verbose:
            logger.info(message, *args)
        else:
            logger.debug(message, *args)

    def describe_setup(self) -> None:
        """Log what this engine is going to do."""
        self.log("Preset: %s", self.config.preset)
        if self.config.preset == "genshin" and self.genshin is not None:
            self.log(
                "Genshin instrument: 21 keys, C3-B5. transpose=%+d, black keys=%s, out-of-range=%s",
                self.genshin.transpose,
                self.config.genshin.accidental,
                self.config.genshin.out_of_range,
            )
        else:
            self.log(
                "Generic binds: %d key, %d mouse button, %d mouse movement",
                len(self._key_binds),
                len(self._button_binds),
                len(self._move_binds),
            )

    # -- translation ------------------------------------------------------

    def resolve(self, note: int) -> Target:
        """Decide what ``note`` maps to under the current preset."""
        if self.genshin is not None:
            plan = self.genshin.plan(note)
            if plan.key is None:
                return Target("unbound", plan=plan)
            return Target("key", key=lookup_key(plan.key), plan=plan)
        if note in self._key_binds:
            return Target("key", key=self._key_binds[note])
        if note in self._button_binds:
            return Target("button", button=self._button_binds[note])
        if note in self._move_binds:
            return Target("move", direction=self._move_binds[note])
        return Target("unbound")

    def press(self, note: int) -> None:
        """Handle a note going down.

        The input goes out first and the note is described afterwards: whatever
        reports on a note must never sit between the note and the game.
        """
        self._maybe_auto_transpose(note)
        target = self.resolve(note)
        sent = self._apply(note, target)
        self._report(note, target, sent)

    def _apply(self, note: int, target: Target) -> bool:
        """Send whatever ``target`` stands for. Returns whether input went out.

        Nothing in here may log: this is the part that has to be fast, and it
        runs before :meth:`_report` gets a chance to write anything.
        """
        if target.kind == "key":
            return self._press_key(note, target)
        if target.kind == "button" and target.button is not None:
            self.mouse.button(target.button, True)
            self._held_buttons[note] = target.button
            self.stats["buttons"] += 1
            return True
        if target.kind == "move" and target.direction is not None:
            self._move_counts[target.direction] += 1
            self.stats["moves"] += 1
            return True
        if target.kind == "unbound" and target.plan is None:
            # A Genshin note that cannot be played is counted as skipped, in
            # _report; "unbound" means there is no binding at all.
            self.stats["unbound"] += 1
        return False

    def _report(self, note: int, target: Target, sent: bool) -> None:
        """Log what :meth:`press` just did."""
        plan = target.plan
        if plan is not None:
            if not plan.playable:
                self.stats["skipped"] += 1
            self.log("%s", plan.summary)
            return
        if target.kind == "unbound":
            self.log("    note %d (%s) is not bound", note, pitch_name(note))
        elif target.kind == "key" and sent:
            self.log("    note %d (%s) -> %s", note, pitch_name(note), target.label)

    def _press_key(self, note: int, target: Target) -> bool:
        """Send the key press for ``target``. Returns whether it went out."""
        assert target.key is not None
        if self._held_keys.get(note) is target.key:
            # The same note arriving again must not double-press a game key.
            logger.debug("note %d is already mapped to %s; ignoring repeat", note, target.key.name)
            return False
        if note in self._held_keys:
            self.release(note)

        self.keys.send(target.key, True)
        self._held_keys[note] = target.key
        self.stats["keys"] += 1
        return True

    def release(self, note: int) -> None:
        """Handle a note being released."""
        key = self._held_keys.pop(note, None)
        if key is not None:
            self.keys.send(key, False)
            return

        button = self._held_buttons.pop(note, None) or self._button_binds.get(note)
        if button is not None:
            self.mouse.button(button, False)
            return

        direction = self._move_binds.get(note)
        if direction is not None and self._move_counts[direction] > 0:
            self._move_counts[direction] -= 1
            if self._move_counts[direction] == 0:
                del self._move_counts[direction]

    def handle(self, transition: tuple[int, bool]) -> None:
        """Apply a ``(note, pressed)`` transition."""
        note, pressed = transition
        if pressed:
            self.press(note)
        else:
            self.release(note)

    def all_off(self) -> None:
        """Release every key and mouse button, and stop mouse movement.

        Called on shutdown and on device loss, so nothing is left latched down
        while MIDIator is not listening.
        """
        for key in self._held_keys.values():
            self.keys.send(key, False)
        self._held_keys.clear()
        for button in self._held_buttons.values():
            self.mouse.button(button, False)
        self._held_buttons.clear()
        self._move_counts.clear()
        self.notes.all_off()

    @property
    def has_movement_binds(self) -> bool:
        """Whether any note is bound to move the cursor."""
        return bool(self._move_binds)

    # -- auto transpose ---------------------------------------------------

    def _maybe_auto_transpose(self, note: int) -> None:
        if not (self.config.preset == "genshin" and self.config.genshin.auto_transpose):
            return
        if self._auto_transpose_done:
            return
        self._auto_notes.append(note)
        if len(self._auto_notes) < AUTO_TRANSPOSE_SAMPLE:
            return

        transpose, score = suggest_transpose(self._auto_notes, accidental=self.config.genshin.accidental)
        self.genshin = GenshinInstrumentMapping(
            transpose=transpose,
            accidental=self.config.genshin.accidental,
            out_of_range=self.config.genshin.out_of_range,
        )
        self._auto_notes = []
        self._auto_transpose_done = True
        self.log(
            "Auto transpose: %+d semitones (plays %.0f%% of the first %d notes)",
            transpose,
            score * 100,
            AUTO_TRANSPOSE_SAMPLE,
        )

    # -- mouse movement ---------------------------------------------------

    @property
    def mouse_vector(self) -> tuple[int, int]:
        """The current cursor velocity, from every direction bind that is held."""
        dx = dy = 0
        for direction, count in self._move_counts.items():
            vx, vy = direction.vector
            dx += vx * count
            dy += vy * count
        return dx, dy

    def _mouse_loop(self) -> None:
        """Push the cursor along :attr:`mouse_vector` until stopped."""
        refresh = self.config.windows.mouse_refresh
        sensitivity = self.config.windows.mouse_sensitivity
        while not self._stop.wait(refresh):
            dx, dy = self.mouse_vector
            if dx == 0 and dy == 0:
                continue
            if dx and dy:
                # Damp diagonals so they travel no faster than cardinals do.
                dx, dy = dx * sensitivity / sqrt(2), dy * sensitivity / sqrt(2)
            else:
                dx, dy = dx * sensitivity, dy * sensitivity
            self.mouse.move(int(dx), int(dy))

    def start_mouse_thread(self) -> None:
        """Begin moving the cursor in the background, if anything can move it."""
        if not self.has_movement_binds:
            # The Genshin preset never moves the cursor, and a generic config
            # need not either, so do not spin up a thread that can only idle.
            self.log("No mouse movement binds; not starting the mouse handler.")
            return
        if self._move_thread is not None:
            return
        self._stop.clear()
        self._move_thread = threading.Thread(target=self._mouse_loop, name="midiator-mouse", daemon=True)
        self._move_thread.start()
        self.log("Mouse handler thread started.")

    # -- shutdown ---------------------------------------------------------

    def stop(self) -> None:
        """Release everything and stop the mouse thread. Safe to call twice."""
        if self._stop.is_set() and self._move_thread is None:
            return
        self._stop.set()
        self.all_off()
        thread, self._move_thread = self._move_thread, None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2 * self.config.windows.mouse_refresh + 0.5)
        self.log(
            "Stopped. Sent %d key presses, %d mouse buttons, %d movement binds; skipped %d unplayable notes.",
            self.stats["keys"],
            self.stats["buttons"],
            self.stats["moves"],
            self.stats["skipped"],
        )


def preview(notes: list[int], config: Config) -> list[str]:
    """Describe how ``notes`` would map, without sending any input.

    Respects the configured preset: the Genshin layout under ``"genshin"``, the
    explicit binds under ``"generic"``.
    """
    if config.preset == "genshin":
        mapping = GenshinInstrumentMapping(
            transpose=config.genshin.transpose,
            accidental=config.genshin.accidental,
            out_of_range=config.genshin.out_of_range,
        )
        return [mapping.plan(note).summary for note in notes]

    engine = Midiator(config, _NullKeySender(), _NullMouseSender())
    return [f"{pitch_name(note):>4} -> {engine.resolve(note).label}" for note in notes]


class _NullKeySender:
    """A :class:`KeySender` that drops events; used by :func:`preview`."""

    def send(self, key: Key, pressed: bool) -> None:
        """Do nothing."""


class _NullMouseSender:
    """A :class:`MouseSender` that drops events; used by :func:`preview`."""

    def move(self, dx: int, dy: int) -> None:
        """Do nothing."""

    def button(self, button: MouseButton, pressed: bool) -> None:
        """Do nothing."""
