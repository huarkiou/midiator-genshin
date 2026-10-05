"""The note-driven engine: turns MIDI transitions into Windows input events."""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from math import sqrt
from typing import Protocol, runtime_checkable

import mido

from midiator.config import Config
from midiator.midi import HeldNotes
from midiator.notes import GenshinInstrumentMapping, NotePlan, pitch_name, suggest_transpose
from midiator.windows.keyboard import Key, lookup_key
from midiator.windows.mouse import Direction, MouseFlag

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

    def button(self, flag: MouseFlag) -> None:
        """Send a mouse button event."""


@dataclass(frozen=True, slots=True)
class Target:
    """What one note does when it goes down."""

    kind: str
    """``"key"``, ``"button"``, ``"move"`` or ``"unbound"``."""
    key: Key | None = None
    button: MouseFlag | None = None
    direction: Direction | None = None
    plan: NotePlan | None = None

    @property
    def label(self) -> str:
        """A short name for the target, for logs."""
        if self.kind == "key" and self.key is not None:
            return f"Key {self.key.name}"
        if self.kind == "button" and self.button is not None:
            return f"Mouse {self.button.name}"
        if self.kind == "move" and self.direction is not None:
            return f"Mouse {self.direction.name}"
        return "Key not bound."


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

    def _build_button_binds(self) -> dict[int, MouseFlag]:
        if self.genshin is not None:
            return {}
        binds: dict[int, MouseFlag] = {}
        for note, name in self.config.binds.mouse_buttons.items():
            try:
                binds[note] = MouseFlag[name.strip().upper()]
            except KeyError:
                raise KeyError(f"unknown mouse button {name!r}; try one of {[f.name for f in MouseFlag]}") from None
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
        """Handle a note going down."""
        self._maybe_auto_transpose(note)
        target = self.resolve(note)

        if target.plan is not None:
            line = target.plan.summary
            if not target.plan.playable:
                self.stats["skipped"] += 1
            self.log("%s", line)

        if target.kind == "unbound":
            if target.plan is None:
                self.log("    note %d (%s) is not bound", note, pitch_name(note))
                self.stats["unbound"] += 1
            return

        if target.kind == "key":
            self._press_key(note, target)
        elif target.kind == "button" and target.button is not None:
            self.mouse.button(target.button)
            self.stats["buttons"] += 1
        elif target.kind == "move" and target.direction is not None:
            self._move_counts[target.direction] += 1
            self.stats["moves"] += 1

    def _press_key(self, note: int, target: Target) -> None:
        assert target.key is not None
        if self._held_keys.get(note) is target.key:
            # The same note arriving again must not double-press a game key.
            logger.debug("note %d is already mapped to %s; ignoring repeat", note, target.key.name)
            return
        if note in self._held_keys:
            self.release(note)

        if target.plan is None:
            self.log("    note %d (%s) -> %s", note, pitch_name(note), target.label)
        self.keys.send(target.key, True)
        self._held_keys[note] = target.key
        self.stats["keys"] += 1

    def release(self, note: int) -> None:
        """Handle a note being released."""
        key = self._held_keys.pop(note, None)
        if key is not None:
            self.keys.send(key, False)
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
        """Release every key and stop any mouse movement this engine started."""
        for key in self._held_keys.values():
            self.keys.send(key, False)
        self._held_keys.clear()
        self._move_counts.clear()
        self.notes.all_off()

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
        """Begin moving the cursor in the background."""
        if self._move_thread is not None:
            return
        self._stop.clear()
        self._move_thread = threading.Thread(target=self._mouse_loop, name="midiator-mouse", daemon=True)
        self._move_thread.start()
        self.log("Mouse handler thread started.")

    # -- main loop --------------------------------------------------------

    def run(self, messages: Iterator[mido.Message]) -> None:
        """Consume ``messages`` until the iterator ends or :meth:`stop` is called."""
        self.describe_setup()
        self.start_mouse_thread()
        self.log("Handling MIDI events. Press Ctrl+C to stop.")
        try:
            for message in messages:
                if self._stop.is_set():
                    break
                transition = self.notes.update(message)
                if transition is not None:
                    self.handle(transition)
        finally:
            self.stop()

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
    """Describe how ``notes`` would map, without sending any input."""
    mapping = GenshinInstrumentMapping(
        transpose=config.genshin.transpose,
        accidental=config.genshin.accidental,
        out_of_range=config.genshin.out_of_range,
    )
    return [mapping.plan(note).summary for note in notes]
