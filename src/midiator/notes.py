"""Genshin Impact instrument key layout and MIDI pitch mapping.

Every playable Genshin instrument exposes the same 21-key keyboard layout: the
seven naturals of three consecutive octaves, with no black keys.

===========  ============  ============
Register     MIDI pitches  Keys
===========  ============  ============
Low          C3-B3         ``Z X C V B N M``
Middle       C4-B4         ``A S D F G H J``
High         C5-B5         ``Q W E R T Y U``
===========  ============  ============

Because Genshin has no black keys, an arbitrary MIDI performance cannot be
played literally. Two adjustments are available, applied in this order:

1. **Octave folding** moves a pitch by whole octaves until it lands in
   C3 (48) to B5 (83).
2. **Accidental snapping** moves a pitch onto a neighbouring natural key.
   When both neighbours are equally close, the lower one wins.
"""

from __future__ import annotations

from dataclasses import dataclass

#: MIDI pitch of the lowest playable key (C3).
LOWEST_PITCH = 48
#: MIDI pitch of the highest playable key (B5).
HIGHEST_PITCH = 83

#: The three registers, low to high, each mapping a natural pitch to its key.
REGISTERS: tuple[tuple[int, str], ...] = (
    (48, "Z X C V B N M"),
    (60, "A S D F G H J"),
    (72, "Q W E R T Y U"),
)

#: Semitone offsets of the seven naturals within an octave (C D E F G A B).
NATURAL_OFFSETS: tuple[int, ...] = (0, 2, 4, 5, 7, 9, 11)

#: Qualities of the twelve chromatic pitches within an octave.
_QUALITIES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

#: MIDI pitch -> game key, built from :data:`REGISTERS`.
LYRE_KEYS: dict[int, str] = {
    base + offset: key for base, keys in REGISTERS for offset, key in zip(NATURAL_OFFSETS, keys.split(), strict=True)
}

#: The 21 playable pitches, ascending.
PLAYABLE_PITCHES: tuple[int, ...] = tuple(sorted(LYRE_KEYS))


def pitch_name(pitch: int) -> str:
    """Render a MIDI pitch as a scientific pitch name, e.g. ``42`` -> ``"F#3"``.

    MIDI 60 is C4, so the octave number is ``pitch // 12 - 1``.
    """
    return f"{_QUALITIES[pitch % 12]}{pitch // 12 - 1}"


def is_natural(pitch: int) -> bool:
    """Whether ``pitch`` is a white key."""
    return pitch % 12 in NATURAL_OFFSETS


def is_playable(pitch: int) -> bool:
    """Whether ``pitch`` already maps to one of the 21 keys."""
    return pitch in LYRE_KEYS


def key_for_pitch(pitch: int) -> str | None:
    """Return the game key for ``pitch``, or ``None`` if it is not playable."""
    return LYRE_KEYS.get(pitch)


def fold_into_range(pitch: int) -> int:
    """Shift ``pitch`` by whole octaves until it lies between C3 and B5."""
    while pitch < LOWEST_PITCH:
        pitch += 12
    while pitch > HIGHEST_PITCH:
        pitch -= 12
    return pitch


def snap_to_natural(pitch: int) -> int:
    """Move ``pitch`` to the nearest natural key, preferring the lower one.

    The result is guaranteed to be a natural pitch but is *not* guaranteed to
    lie inside the playable register; combine with :func:`fold_into_range`.
    """
    if is_natural(pitch):
        return pitch
    down, up = pitch - 1, pitch + 1
    while not is_natural(down) and not is_natural(up):
        down -= 1
        up += 1
    # A black key is always adjacent to naturals on both sides, so exactly one
    # step suffices; the loop above only exists to make the invariant explicit.
    return down if is_natural(down) else up


def snap_into_range(pitch: int) -> int | None:
    """Snap ``pitch`` to the nearest *playable* key, or ``None`` if impossible.

    Candidates are gathered by folding first and then snapping, which preserves
    the neighbours that folding could otherwise push out of the register.
    """
    if pitch in LYRE_KEYS:
        return pitch

    candidates: set[int] = set()
    for base in {pitch, fold_into_range(pitch)}:
        snapped = snap_to_natural(base)
        for neighbour in (snapped, snapped - 12, snapped + 12):
            if neighbour in LYRE_KEYS:
                candidates.add(neighbour)
    if not candidates:
        return None
    # Nearest first, then lower pitch — matching snap_to_natural's tie-break.
    return min(candidates, key=lambda candidate: (abs(candidate - pitch), candidate))


@dataclass(frozen=True, slots=True)
class NotePlan:
    """What a single incoming MIDI note turns into."""

    midi_note: int
    pitch: int | None
    key: str | None
    transposed: bool
    folded: bool
    snapped: bool
    playable: bool

    @property
    def summary(self) -> str:
        """A short human-readable description for logs."""
        if self.pitch is None or self.key is None:
            return f"{pitch_name(self.midi_note):>4} -> skipped"
        flags = []
        if self.transposed:
            flags.append("transposed")
        if self.folded:
            flags.append("folded")
        if self.snapped:
            flags.append("snapped")
        suffix = f" ({', '.join(flags)})" if flags else ""
        return f"{pitch_name(self.midi_note):>4} -> {pitch_name(self.pitch):>4} -> key {self.key}{suffix}"


#: How to treat notes that have no key of their own.
ACCIDENTAL_POLICIES = ("snap", "skip")

#: What to do with notes outside the three playable octaves.
OUT_OF_RANGE_POLICIES = ("fold", "skip")


class GenshinInstrumentMapping:
    """Maps MIDI pitches onto the 21 keys of a Genshin instrument.

    :param transpose: Semitones to shift every note by, applied first.
    :param accidental: ``"snap"`` to move black keys to a neighbour, ``"skip"``
        to drop them.
    :param out_of_range: ``"fold"`` to shift by octaves into C3-B5, ``"skip"``
        to drop the note.
    """

    def __init__(
        self,
        transpose: int = 0,
        accidental: str = "snap",
        out_of_range: str = "fold",
    ) -> None:
        if accidental not in ACCIDENTAL_POLICIES:
            raise ValueError(f"accidental must be one of {ACCIDENTAL_POLICIES}, got {accidental!r}")
        if out_of_range not in OUT_OF_RANGE_POLICIES:
            raise ValueError(f"out_of_range must be one of {OUT_OF_RANGE_POLICIES}, got {out_of_range!r}")
        self.transpose = transpose
        self.accidental = accidental
        self.out_of_range = out_of_range

    def plan(self, midi_note: int) -> NotePlan:
        """Work out which key ``midi_note`` should play, if any."""
        shifted = midi_note + self.transpose
        transposed = self.transpose != 0

        def give_up() -> NotePlan:
            return NotePlan(midi_note, None, None, transposed, False, False, False)

        if self.out_of_range == "skip" and not (LOWEST_PITCH <= shifted <= HIGHEST_PITCH):
            return give_up()

        if is_natural(shifted):
            pitch = fold_into_range(shifted) if self.out_of_range == "fold" else shifted
            folded = pitch != shifted
            key = key_for_pitch(pitch)
            if key is None:
                return give_up()
            return NotePlan(midi_note, pitch, key, transposed, folded, False, True)

        # A black key after transposition.
        if self.accidental == "skip":
            return give_up()

        pitch = snap_into_range(shifted)
        if pitch is None:
            return give_up()
        # Snapping only ever moves a note by a semitone, so a larger gap means
        # octave folding also took part.
        folded = self.out_of_range == "fold" and abs(pitch - shifted) > 1
        key = key_for_pitch(pitch)
        if key is None:  # pragma: no cover - snap_into_range only returns playable pitches
            return give_up()
        return NotePlan(midi_note, pitch, key, transposed, folded, True, True)


def playable_ratio(notes: list[int], mapping: GenshinInstrumentMapping) -> float:
    """Fraction of ``notes`` that ``mapping`` can play, for tuning transpose."""
    if not notes:
        return 1.0
    return sum(1 for note in notes if mapping.plan(note).playable) / len(notes)


def suggest_transpose(notes: list[int], accidental: str = "snap", span: int = 12) -> tuple[int, float]:
    """Suggest the transpose in ``-span..span`` that plays the most of ``notes``.

    Returns the best transpose and the fraction of notes it can play. Ties go to
    the smaller absolute shift.
    """
    best_transpose, best_score = 0, -1.0
    for transpose in sorted(range(-span, span + 1), key=abs):
        score = playable_ratio(notes, GenshinInstrumentMapping(transpose=transpose, accidental=accidental))
        if score > best_score:
            best_transpose, best_score = transpose, score
    return best_transpose, best_score
