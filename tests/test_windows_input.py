"""Tests for the Windows input tables and senders."""

from __future__ import annotations

import ctypes
from typing import Any

import pytest

from midiator.windows.keyboard import (
    KEYBOARD,
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEYEVENTF_SCANCODE,
    WindowsKeySender,
    lookup_key,
    normalise_key_name,
)
from midiator.windows.mouse import Direction, MouseFlag, WindowsMouseSender


class TestKeyTable:
    def test_the_21_genshin_keys_exist(self) -> None:
        for letter in "ZXCVBNMASDFGHJQWERTYU":
            assert letter in KEYBOARD

    def test_genshin_scan_codes_match_windows(self) -> None:
        user32 = ctypes.windll.user32
        for letter in "ZXCVBNMASDFGHJQWERTYU":
            key = KEYBOARD[letter]
            mapped = user32.MapVirtualKeyW(key.vk, 0) & 0xFF
            assert mapped == key.scancode, letter

    def test_letters_and_digits_have_expected_codes(self) -> None:
        assert (KEYBOARD["A"].vk, KEYBOARD["A"].scancode) == (0x41, 0x1E)
        assert (KEYBOARD["Z"].vk, KEYBOARD["Z"].scancode) == (0x5A, 0x2C)
        assert (KEYBOARD["ONE"].vk, KEYBOARD["ONE"].scancode) == (0x31, 0x02)
        assert (KEYBOARD["SPACE"].vk, KEYBOARD["SPACE"].scancode) == (0x20, 0x39)

    def test_extended_keys_are_flagged(self) -> None:
        for name in ("UP", "DOWN", "LEFT", "RIGHT", "HOME", "END", "INSERT", "DELETE", "DIVIDE", "NUMPADENTER"):
            assert KEYBOARD[name].extended, name
        for name in ("A", "Z", "SPACE", "NUMPAD0", "F1"):
            assert not KEYBOARD[name].extended, name

    def test_scancodes_are_a_single_byte(self) -> None:
        for key in KEYBOARD.values():
            assert 0 < key.scancode <= 0xFF, key.name
            assert 0 < key.vk <= 0xFF, key.name


class TestLookup:
    @pytest.mark.parametrize("name", ["a", "A", "key_a", "KEY_A", " Key_A "])
    def test_letters_accept_common_spellings(self, name: str) -> None:
        assert lookup_key(name).name == "A"

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [("esc", "ESCAPE"), ("enter", "RETURN"), ("ctrl", "LCONTROL"), ("shift", "LSHIFT"), ("alt", "LMENU")],
    )
    def test_aliases_resolve(self, alias: str, canonical: str) -> None:
        assert lookup_key(alias).name == canonical

    def test_unknown_key_raises_with_guidance(self) -> None:
        with pytest.raises(KeyError, match="unknown key"):
            lookup_key("BANANA")

    def test_normalise_is_case_insensitive(self) -> None:
        assert normalise_key_name("key_w") == "W"


class TestKeySender:
    def test_scancode_mode_uses_scan_codes(self) -> None:
        sender = WindowsKeySender(mode="scancode")
        w_vk, w_scan, flags = sender._resolve(KEYBOARD["A"])
        assert (w_vk, w_scan) == (0, 0x1E)
        assert flags & KEYEVENTF_SCANCODE

    def test_virtualkey_mode_uses_virtual_keys(self) -> None:
        sender = WindowsKeySender(mode="virtualkey")
        w_vk, w_scan, flags = sender._resolve(KEYBOARD["A"])
        assert (w_vk, w_scan) == (0x41, 0)
        assert not flags & KEYEVENTF_SCANCODE

    def test_extended_keys_set_the_extended_flag(self) -> None:
        sender = WindowsKeySender()
        _, _, flags = sender._resolve(KEYBOARD["UP"])
        assert flags & KEYEVENTF_EXTENDEDKEY

    def test_unknown_mode_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="scancode"):
            WindowsKeySender(mode="psychic")

    def test_dry_run_does_not_call_send_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = False

        def fake_send_input(*args: object) -> int:
            nonlocal called
            called = True
            return 1

        monkeypatch.setattr(ctypes.windll.user32, "SendInput", fake_send_input, raising=False)
        WindowsKeySender(dry_run=True).send(KEYBOARD["A"], True)
        assert called is False

    def test_press_and_release_differ_only_by_the_keyup_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[tuple[int, int, int]] = []
        real_send_input = ctypes.windll.user32.SendInput

        def fake_send_input(count: int, events: Any, size: int) -> int:
            event = events[0]
            captured.append((event.ii.ki.wVk, event.ii.ki.wScan, event.ii.ki.dwFlags))
            return 1

        monkeypatch.setattr(ctypes.windll.user32, "SendInput", fake_send_input, raising=False)
        try:
            sender = WindowsKeySender()
            sender.send(KEYBOARD["A"], True)
            sender.send(KEYBOARD["A"], False)
        finally:
            monkeypatch.setattr(ctypes.windll.user32, "SendInput", real_send_input, raising=False)

        assert len(captured) == 2
        assert captured[0][2] & KEYEVENTF_SCANCODE
        assert not captured[0][2] & KEYEVENTF_KEYUP
        assert captured[1][2] & KEYEVENTF_KEYUP
        assert captured[0][:2] == captured[1][:2]


class TestMouseTables:
    def test_direction_vectors(self) -> None:
        assert Direction.LEFT.vector == (-1, 0)
        assert Direction.RIGHT.vector == (1, 0)
        assert Direction.UP.vector == (0, -1)
        assert Direction.DOWN.vector == (0, 1)
        assert Direction.STAY.vector == (0, 0)

    def test_button_flags_are_distinct(self) -> None:
        values = [flag.value for flag in MouseFlag]
        assert len(values) == len(set(values))

    def test_zero_movement_sends_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = False

        def fake_send_input(*args: object) -> int:
            nonlocal called
            called = True
            return 1

        monkeypatch.setattr(ctypes.windll.user32, "SendInput", fake_send_input, raising=False)
        WindowsMouseSender().move(0, 0)
        assert called is False
