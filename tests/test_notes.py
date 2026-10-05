"""Tests for the Genshin instrument layout and pitch mapping."""

from __future__ import annotations

import pytest

from midiator.notes import (
    HIGHEST_PITCH,
    LOWEST_PITCH,
    LYRE_KEYS,
    PLAYABLE_PITCHES,
    GenshinInstrumentMapping,
    fold_into_range,
    is_natural,
    is_playable,
    key_for_pitch,
    pitch_name,
    playable_ratio,
    snap_into_range,
    snap_to_natural,
    suggest_transpose,
)


class TestLayout:
    def test_has_21_keys(self) -> None:
        assert len(LYRE_KEYS) == 21
        assert len(PLAYABLE_PITCHES) == 21

    def test_register_assignment_matches_the_game(self) -> None:
        low = [LYRE_KEYS[p] for p in (48, 50, 52, 53, 55, 57, 59)]
        middle = [LYRE_KEYS[p] for p in (60, 62, 64, 65, 67, 69, 71)]
        high = [LYRE_KEYS[p] for p in (72, 74, 76, 77, 79, 81, 83)]
        assert low == list("ZXCVBNM")
        assert middle == list("ASDFGHJ")
        assert high == list("QWERTYU")

    def test_span_is_three_octaves(self) -> None:
        assert LOWEST_PITCH == 48
        assert HIGHEST_PITCH == 83
        assert PLAYABLE_PITCHES[0] == LOWEST_PITCH
        assert PLAYABLE_PITCHES[-1] == HIGHEST_PITCH

    def test_naturals_only(self) -> None:
        assert all(is_natural(pitch) for pitch in PLAYABLE_PITCHES)

    def test_every_key_is_used_exactly_once(self) -> None:
        assert sorted(LYRE_KEYS.values()) == sorted("ZXCVBNMASDFGHJQWERTYU")


class TestPitchNames:
    @pytest.mark.parametrize(
        ("pitch", "expected"),
        [(60, "C4"), (69, "A4"), (48, "C3"), (83, "B5"), (42, "F#2"), (61, "C#4"), (0, "C-1")],
    )
    def test_names_follow_scientific_pitch_notation(self, pitch: int, expected: str) -> None:
        assert pitch_name(pitch) == expected

    def test_playable_notes_read_as_their_key(self) -> None:
        assert key_for_pitch(60) == "A"
        assert key_for_pitch(48) == "Z"
        assert key_for_pitch(83) == "U"
        assert key_for_pitch(61) is None


class TestFold:
    @pytest.mark.parametrize(
        ("pitch", "expected"),
        [
            (48, 48),  # already C3
            (83, 83),  # already B5
            (47, 59),  # B2 -> B3
            (36, 48),  # C2 -> C3
            (24, 48),  # C1 -> C3
            (84, 72),  # C6 -> C5
            (95, 83),  # B6 -> B5
            (96, 72),  # C7 -> C5
            (107, 83),  # B7 -> B5
        ],
    )
    def test_folds_by_octaves_into_range(self, pitch: int, expected: int) -> None:
        assert fold_into_range(pitch) == expected

    def test_folding_preserves_pitch_class(self) -> None:
        for pitch in list(range(0, 128)):
            assert fold_into_range(pitch) % 12 == pitch % 12

    def test_folding_lands_inside_the_register(self) -> None:
        for pitch in range(128):
            assert LOWEST_PITCH <= fold_into_range(pitch) <= HIGHEST_PITCH


class TestSnap:
    @pytest.mark.parametrize(
        ("pitch", "expected"),
        [(61, 60), (63, 62), (66, 65), (68, 67), (70, 69), (49, 48), (51, 50)],
    )
    def test_black_keys_prefer_the_lower_neighbour(self, pitch: int, expected: int) -> None:
        assert snap_to_natural(pitch) == expected

    def test_naturals_are_untouched(self) -> None:
        for pitch in range(128):
            if is_natural(pitch):
                assert snap_to_natural(pitch) == pitch

    def test_snapping_always_lands_on_a_natural(self) -> None:
        for pitch in range(128):
            assert is_natural(snap_to_natural(pitch))

    @pytest.mark.parametrize("pitch", [49, 51, 61, 63, 66, 68, 70, 73, 75, 78, 80, 82])
    def test_in_range_black_keys_snap_to_a_playable_key(self, pitch: int) -> None:
        snapped = snap_into_range(pitch)
        assert snapped is not None
        assert is_playable(snapped)

    def test_every_pitch_in_the_full_midi_range_maps(self) -> None:
        # With snap + fold enabled, no pitch should be impossible to place.
        for pitch in range(128):
            snapped = snap_into_range(pitch)
            assert snapped is not None, pitch
            assert is_playable(snapped), pitch

    def test_very_high_pitch_resolves_inside_the_register(self) -> None:
        # G9 folds to G5 rather than clamping to the top key B5.
        assert snap_into_range(127) == 79  # G5
        assert snap_into_range(0) == 48  # C3

    def test_out_of_range_notes_fold_towards_the_register(self) -> None:
        assert snap_into_range(84) == 72  # C6 -> C5
        assert snap_into_range(85) == 72  # C#6 -> C5
        assert snap_into_range(95) == 83  # B6 -> B5
        assert snap_into_range(36) == 48  # C2 -> C3


class TestMapping:
    def test_natural_in_range_is_unmodified(self) -> None:
        plan = GenshinInstrumentMapping().plan(60)
        assert plan.playable
        assert plan.key == "A"
        assert plan.pitch == 60
        assert not plan.snapped
        assert not plan.folded
        assert not plan.transposed

    def test_black_key_is_snapped(self) -> None:
        plan = GenshinInstrumentMapping().plan(61)
        assert plan.playable
        assert plan.key == "A"
        assert plan.pitch == 60
        assert plan.snapped

    def test_high_note_is_folded_into_the_top_register(self) -> None:
        plan = GenshinInstrumentMapping().plan(84)  # C6 -> C5
        assert plan.playable
        assert plan.key == "Q"
        assert plan.pitch == 72
        assert plan.folded

    def test_low_note_is_folded(self) -> None:
        plan = GenshinInstrumentMapping().plan(36)  # C2 -> C3
        assert plan.playable
        assert plan.key == "Z"
        assert plan.pitch == 48
        assert plan.folded

    def test_note_between_the_registers_keeps_its_own_octave(self) -> None:
        # B6 folds down to B5, not all the way to the bottom.
        plan = GenshinInstrumentMapping().plan(95)
        assert plan.key == "U"
        assert plan.pitch == 83
        assert plan.folded

    def test_transpose_shifts_first(self) -> None:
        plan = GenshinInstrumentMapping(transpose=2).plan(58)  # A#3 -> C4
        assert plan.playable
        assert plan.key == "A"
        assert plan.transposed

    def test_skip_policy_drops_black_keys(self) -> None:
        plan = GenshinInstrumentMapping(accidental="skip").plan(61)
        assert not plan.playable
        assert plan.key is None

    def test_skip_policy_drops_out_of_range_notes(self) -> None:
        plan = GenshinInstrumentMapping(out_of_range="skip").plan(84)
        assert not plan.playable
        assert plan.key is None

    def test_skip_policy_keeps_in_range_naturals(self) -> None:
        plan = GenshinInstrumentMapping(out_of_range="skip", accidental="skip").plan(60)
        assert plan.playable
        assert plan.key == "A"

    def test_invalid_policy_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="accidental"):
            GenshinInstrumentMapping(accidental="maybe")
        with pytest.raises(ValueError, match="out_of_range"):
            GenshinInstrumentMapping(out_of_range="maybe")

    def test_summary_names_the_source_and_destination(self) -> None:
        assert "C#4" in GenshinInstrumentMapping().plan(61).summary
        assert "skipped" in GenshinInstrumentMapping(accidental="skip").plan(61).summary

    def test_every_pitch_maps_under_default_policies(self) -> None:
        mapping = GenshinInstrumentMapping()
        for pitch in range(128):
            plan = mapping.plan(pitch)
            assert plan.playable, pitch
            assert plan.key in LYRE_KEYS.values()


class TestTransposeSuggestion:
    def test_already_playable_music_needs_no_transpose(self) -> None:
        transpose, score = suggest_transpose([60, 62, 64, 65])
        assert transpose == 0
        assert score == 1.0

    def test_snapping_makes_every_transpose_equally_playable(self) -> None:
        # With snapping on there is always a key for every note, so the
        # suggestion falls back to no shift. That is why transpose tuning is
        # only meaningful together with accidental = "skip".
        for transpose in range(-12, 13):
            mapping = GenshinInstrumentMapping(transpose=transpose)
            assert playable_ratio([60, 62, 64, 65], mapping) == 1.0
        assert suggest_transpose([60, 62, 64, 65]) == (0, 1.0)

    def test_sharp_heavy_music_suggests_a_shift_when_skipping(self) -> None:
        # Five of these six notes are black keys; with skipping on only B4 (71)
        # survives, and a one-semitone shift makes the whole run playable.
        notes = [66, 68, 70, 71, 73, 75]
        transpose, score = suggest_transpose(notes, accidental="skip")
        assert score == 1.0
        assert transpose == 1
        assert playable_ratio(notes, GenshinInstrumentMapping(accidental="skip")) == pytest.approx(1 / 6)

    def test_empty_input_is_free(self) -> None:
        assert suggest_transpose([]) == (0, 1.0)

    def test_ratio_reflects_playability(self) -> None:
        mapping = GenshinInstrumentMapping(accidental="skip")
        assert playable_ratio([60, 62], mapping) == 1.0
        assert playable_ratio([60, 61], mapping) == 0.5
        assert playable_ratio([], mapping) == 1.0
