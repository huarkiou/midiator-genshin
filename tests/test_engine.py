"""Translation tests: MIDI messages in, recorded key/mouse events out."""

from __future__ import annotations

from typing import TypedDict, Unpack

import mido
import pytest
from conftest import RecordingKeySender, RecordingMouseSender

from midiator.config import Config, GenshinSettings, MidiSettings
from midiator.engine import Midiator
from midiator.midi import HeldNotes


class GenshinOptions(TypedDict, total=False):
    """Optional Genshin preset overrides, so tests can pass kwargs safely."""

    transpose: int
    accidental: str
    out_of_range: str
    auto_transpose: bool


def make_engine(
    *,
    preset: str = "genshin",
    keys: RecordingKeySender,
    mouse: RecordingMouseSender,
    channel: int | None = 0,
    **genshin: Unpack[GenshinOptions],
) -> Midiator:
    config = Config(preset=preset, midi=MidiSettings(channel=channel))
    if genshin:
        config.genshin = GenshinSettings(**genshin)
    config.verbose = False
    return Midiator(config, keys, mouse)


class TestHeldNotes:
    def test_note_on_then_off_is_a_full_cycle(self) -> None:
        held = HeldNotes()
        assert held.update(mido.Message("note_on", note=60, velocity=100)) == (60, True)
        assert 60 in held
        assert held.update(mido.Message("note_off", note=60, velocity=0)) == (60, False)
        assert 60 not in held

    def test_note_on_with_zero_velocity_counts_as_release(self) -> None:
        held = HeldNotes()
        held.update(mido.Message("note_on", note=60, velocity=100))
        assert held.update(mido.Message("note_on", note=60, velocity=0)) == (60, False)

    def test_repeated_note_on_is_ignored(self) -> None:
        held = HeldNotes()
        held.update(mido.Message("note_on", note=60, velocity=100))
        assert held.update(mido.Message("note_on", note=60, velocity=64)) is None

    def test_release_without_press_is_ignored(self) -> None:
        assert HeldNotes().update(mido.Message("note_off", note=60, velocity=0)) is None

    def test_channel_filter(self) -> None:
        held = HeldNotes(channel=0)
        assert held.update(mido.Message("note_on", note=60, velocity=1, channel=1)) is None
        assert held.update(mido.Message("note_on", note=60, velocity=1, channel=0)) == (60, True)

    def test_none_channel_accepts_everything(self) -> None:
        held = HeldNotes(channel=None)
        assert held.update(mido.Message("note_on", note=60, velocity=1, channel=9)) == (60, True)

    def test_non_note_messages_are_ignored(self) -> None:
        held = HeldNotes()
        assert held.update(mido.Message("control_change", control=64, value=127)) is None

    def test_all_off_reports_and_clears(self) -> None:
        held = HeldNotes()
        held.update(mido.Message("note_on", note=60, velocity=1))
        held.update(mido.Message("note_on", note=64, velocity=1))
        assert held.all_off() == (60, 64)
        assert len(held) == 0


class TestGenshinTranslation:
    def test_note_on_presses_and_note_off_releases(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        engine.press(60)
        assert key_sender.events == [("A", True)]
        engine.release(60)
        assert key_sender.events == [("A", True), ("A", False)]

    def test_one_note_maps_to_one_key_across_registers(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        for note, expected in [(48, "Z"), (60, "A"), (72, "Q"), (83, "U")]:
            key_sender.clear()
            engine.press(note)
            engine.release(note)
            assert key_sender.events == [(expected, True), (expected, False)]

    def test_black_key_snaps_to_the_key_below(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        engine.press(61)  # C#4 -> C4 -> A
        assert key_sender.presses == ["A"]
        plan = engine.resolve(61).plan
        assert plan is not None
        assert plan.snapped

    def test_out_of_range_note_folds_in(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        engine.press(84)  # C6 -> C5 -> Q
        assert key_sender.presses == ["Q"]

    def test_folding_reaches_the_bottom_key(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        engine.press(36)  # C2 -> C3 -> Z
        assert key_sender.presses == ["Z"]

    def test_unplayable_note_sends_nothing(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender, accidental="skip")
        engine.press(61)
        assert key_sender.events == []
        assert engine.stats["skipped"] == 1

    def test_repeated_note_on_does_not_double_press(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        engine.press(60)
        engine.press(60)
        assert key_sender.presses == ["A"]

    def test_release_without_press_is_harmless(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        engine.release(60)
        assert key_sender.events == []

    def test_chord_presses_then_releases_each_key(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        for note in (60, 64, 67):
            engine.press(note)
        assert key_sender.presses == ["A", "D", "G"]
        for note in (60, 64, 67):
            engine.release(note)
        assert key_sender.releases == ["A", "D", "G"]

    def test_all_off_releases_everything(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        engine.press(60)
        engine.press(64)
        key_sender.clear()
        engine.all_off()
        assert sorted(key_sender.releases) == ["A", "D"]

    def test_mouse_is_never_used_by_the_genshin_preset(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        for note in range(128):
            engine.press(note)
        assert mouse_sender.buttons == []
        assert mouse_sender.moves == []

    def test_transpose_applies_to_incoming_notes(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender, transpose=2)
        engine.press(58)  # A#3 + 2 = C4 -> A
        assert key_sender.presses == ["A"]

    def test_press_release_via_messages(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        for message in (
            mido.Message("note_on", note=60, velocity=100),
            mido.Message("note_off", note=60, velocity=0),
        ):
            transition = engine.notes.update(message)
            assert transition is not None
            engine.handle(transition)
        assert key_sender.events == [("A", True), ("A", False)]


class TestAutoTranspose:
    def test_locks_in_after_a_sample(self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender) -> None:
        # Sharp-heavy material is unplayable without a shift when black keys are
        # skipped, which is the case auto-transpose exists to fix.
        engine = make_engine(keys=key_sender, mouse=mouse_sender, auto_transpose=True, accidental="skip")
        for note in [66, 68, 70, 71, 73, 75, 78, 66, 68, 70, 73, 75]:
            engine.press(note)
            engine.release(note)
        assert engine.genshin is not None
        assert engine.genshin.transpose == 1

    def test_nothing_changes_before_the_sample_is_full(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender, auto_transpose=True, accidental="skip")
        for note in [66, 68, 70]:
            engine.press(note)
            engine.release(note)
        assert engine.genshin is not None
        assert engine.genshin.transpose == 0

    def test_off_by_default(self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        for note in [66, 68, 70, 71, 73, 75, 78, 66, 68, 70, 73, 75]:
            engine.press(note)
            engine.release(note)
        assert engine.genshin is not None
        assert engine.genshin.transpose == 0


class TestGenericPreset:
    def _generic(self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender) -> Midiator:
        config = Config(preset="generic", midi=MidiSettings(channel=0))
        config.verbose = False
        config.binds.keys = {42: "W", 45: "SPACE"}
        config.binds.mouse_buttons = {50: "LEFT"}
        config.binds.mouse_movement = {52: "LEFT"}
        return Midiator(config, key_sender, mouse_sender)

    def test_key_binds_are_honoured(self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender) -> None:
        engine = self._generic(key_sender, mouse_sender)
        engine.press(42)
        engine.release(42)
        assert key_sender.events == [("W", True), ("W", False)]

    def test_key_names_accept_a_key_prefix(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        config = Config(preset="generic")
        config.verbose = False
        config.binds.keys = {42: "KEY_W"}
        engine = Midiator(config, key_sender, mouse_sender)
        engine.press(42)
        assert key_sender.presses == ["W"]

    def test_mouse_button_presses_on_note_on_and_releases_on_note_off(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = self._generic(key_sender, mouse_sender)
        engine.press(50)
        assert mouse_sender.buttons == [("LEFT", True)]
        engine.release(50)
        assert mouse_sender.buttons == [("LEFT", True), ("LEFT", False)]
        assert key_sender.events == []

    def test_mouse_button_does_not_stay_held_after_all_off(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = self._generic(key_sender, mouse_sender)
        engine.press(50)
        engine.all_off()
        assert mouse_sender.buttons == [("LEFT", True), ("LEFT", False)]

    def test_all_off_with_nothing_held_emits_nothing(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = self._generic(key_sender, mouse_sender)
        engine.all_off()
        assert key_sender.events == []
        assert mouse_sender.buttons == []

    def test_a_released_note_does_not_disturb_other_notes(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        # 45 is bound to a key, not to the mouse, so releasing it must be inert.
        engine = self._generic(key_sender, mouse_sender)
        engine.release(45)
        assert key_sender.events == []
        assert mouse_sender.buttons == []

    @pytest.mark.parametrize(
        ("written", "suggested"),
        [
            ("LEFT_DOWN", "LEFT"),
            ("LEFT_UP", "LEFT"),
            ("LEFT_CLICK", "LEFT"),
            ("LEFT_RELEASE", "LEFT"),
            ("RIGHT_DOWN", "RIGHT"),
            ("MIDDLE_UP", "MIDDLE"),
        ],
    )
    def test_press_release_flag_names_are_rejected_with_a_migration_hint(
        self,
        written: str,
        suggested: str,
        key_sender: RecordingKeySender,
        mouse_sender: RecordingMouseSender,
    ) -> None:
        config = Config(preset="generic")
        config.binds.mouse_buttons = {50: written}
        with pytest.raises(KeyError, match=rf"Use '{suggested}' instead"):
            Midiator(config, key_sender, mouse_sender)

    def test_movement_binds_are_detected(self, key_sender: RecordingKeySender) -> None:
        config = Config(preset="generic")
        assert Midiator(config, key_sender, RecordingMouseSender()).has_movement_binds is False
        config.binds.mouse_movement = {52: "LEFT"}
        assert Midiator(config, key_sender, RecordingMouseSender()).has_movement_binds is True

    def test_mouse_thread_is_not_started_without_movement_binds(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        """The Genshin preset never moves the cursor, so it must not spawn a thread."""
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        assert engine.has_movement_binds is False
        engine.start_mouse_thread()
        assert engine._move_thread is None

    def test_mouse_thread_starts_when_movement_is_bound(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        config = Config(preset="generic")
        config.verbose = False
        config.binds.mouse_movement = {52: "LEFT"}
        engine = Midiator(config, key_sender, mouse_sender)
        try:
            engine.start_mouse_thread()
            assert engine._move_thread is not None
        finally:
            engine.stop()

    def test_unknown_key_bind_is_rejected(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        config = Config(preset="generic")
        config.binds.keys = {42: "NOT_A_KEY"}
        with pytest.raises(KeyError, match="unknown key"):
            Midiator(config, key_sender, mouse_sender)

    def test_unknown_mouse_button_is_rejected(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        config = Config(preset="generic")
        config.binds.mouse_buttons = {42: "MIDDLE_SIDEWAYS"}
        with pytest.raises(KeyError, match="unknown mouse button"):
            Midiator(config, key_sender, mouse_sender)

    def test_mouse_movement_vector_accumulates_and_cancels(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        config = Config(preset="generic")
        config.verbose = False
        config.binds.mouse_movement = {52: "LEFT", 53: "RIGHT", 54: "UP"}
        engine = Midiator(config, key_sender, mouse_sender)

        engine.press(52)
        assert engine.mouse_vector == (-1, 0)
        engine.press(54)
        assert engine.mouse_vector == (-1, -1)
        engine.press(53)
        assert engine.mouse_vector == (0, -1)
        engine.release(53)
        assert engine.mouse_vector == (-1, -1)
        engine.release(52)
        engine.release(54)
        assert engine.mouse_vector == (0, 0)

    def test_releasing_one_direction_keeps_the_other(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        config = Config(preset="generic")
        config.verbose = False
        config.binds.mouse_movement = {52: "LEFT", 53: "LEFT"}
        engine = Midiator(config, key_sender, mouse_sender)
        engine.press(52)
        engine.press(53)
        assert engine.mouse_vector == (-2, 0)
        engine.release(52)
        assert engine.mouse_vector == (-1, 0)
        engine.release(53)
        assert engine.mouse_vector == (0, 0)

    def test_unbound_note_does_nothing(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = self._generic(key_sender, mouse_sender)
        engine.press(99)
        engine.release(99)
        assert key_sender.events == []
        assert mouse_sender.moves == []
        assert engine.stats["unbound"] == 1


class TestStats:
    def test_counts_each_kind_of_event(
        self, key_sender: RecordingKeySender, mouse_sender: RecordingMouseSender
    ) -> None:
        engine = make_engine(keys=key_sender, mouse=mouse_sender)
        engine.press(60)
        engine.release(60)
        engine.press(61)
        assert engine.stats["keys"] == 2
