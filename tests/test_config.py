"""Tests for configuration loading, validation and rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from midiator.config import Config, load_config, render_default_config, write_default_config


def write(tmp_path: Path, text: str, name: str = "midiator.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestDefaults:
    def test_missing_file_yields_defaults(self) -> None:
        config = Config()
        assert config.preset == "genshin"
        assert config.windows.key_mode == "scancode"
        assert config.genshin.accidental == "snap"
        assert config.genshin.out_of_range == "fold"
        assert config.midi.channel == 0
        assert config.midi.reconnect is True
        assert config.midi.retry_seconds == 2.0
        assert config.midi.watchdog_seconds == 30.0
        assert config.verbose is True

    def test_explicit_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nope.toml")

    def test_rendered_defaults_round_trip(self, tmp_path: Path) -> None:
        path = write(tmp_path, render_default_config())
        config = load_config(path)
        assert config == Config(path=path)
        assert config.preset == "genshin"


class TestParsing:
    def test_reads_every_section(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            """
            preset = "generic"
            verbose = false

            [midi]
            device = "Casio"
            substring = false
            channel = 3
            reconnect = false
            retry_seconds = 0.5
            watchdog_seconds = 12.0

            [windows]
            key_mode = "virtualkey"
            mouse_sensitivity = 8
            mouse_refresh = 0.01

            [genshin]
            transpose = -2
            accidental = "skip"
            out_of_range = "skip"
            auto_transpose = true

            [binds.keys]
            42 = "W"
            "45" = "SPACE"

            [binds.mouse_buttons]
            50 = "LEFT_DOWN"

            [binds.mouse_movement]
            52 = "LEFT"
            """,
        )
        config = load_config(path)
        assert config.preset == "generic"
        assert config.verbose is False
        assert config.midi.device == "Casio"
        assert config.midi.substring is False
        assert config.midi.channel == 3
        assert config.midi.reconnect is False
        assert config.midi.retry_seconds == 0.5
        assert config.midi.watchdog_seconds == 12.0
        assert config.windows.key_mode == "virtualkey"
        assert config.windows.mouse_sensitivity == 8
        assert config.genshin.transpose == -2
        assert config.genshin.accidental == "skip"
        assert config.genshin.auto_transpose is True
        assert config.binds.keys == {42: "W", 45: "SPACE"}
        assert config.binds.mouse_buttons == {50: "LEFT_DOWN"}
        assert config.binds.mouse_movement == {52: "LEFT"}

    def test_partial_file_keeps_other_defaults(self, tmp_path: Path) -> None:
        path = write(tmp_path, "[genshin]\ntranspose = 5\n")
        config = load_config(path)
        assert config.genshin.transpose == 5
        assert config.genshin.accidental == "snap"
        assert config.preset == "genshin"

    def test_bind_notes_may_be_written_as_strings(self, tmp_path: Path) -> None:
        path = write(tmp_path, '[binds.keys]\n"60" = "A"\n')
        assert load_config(path).binds.keys == {60: "A"}

    def test_unknown_section_keys_are_ignored(self, tmp_path: Path) -> None:
        path = write(tmp_path, "[genshin]\ntranspose = 1\nnonsense = true\n")
        assert load_config(path).genshin.transpose == 1

    def test_bad_toml_reports_a_useful_error(self, tmp_path: Path) -> None:
        path = write(tmp_path, "this is not toml = = =\n")
        with pytest.raises(ValueError, match="not valid TOML"):
            load_config(path)

    def test_out_of_range_bind_note_is_dropped(self, tmp_path: Path) -> None:
        path = write(tmp_path, '[binds.keys]\n999 = "W"\n')
        assert load_config(path).binds.keys == {}

    def test_non_numeric_bind_note_is_dropped(self, tmp_path: Path) -> None:
        path = write(tmp_path, '[binds.keys]\n"C4" = "W"\n')
        assert load_config(path).binds.keys == {}


class TestValidation:
    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ('[windows]\nkey_mode = "telepathy"\n', "key_mode"),
            ('[genshin]\naccidental = "maybe"\n', "accidental"),
            ('[genshin]\nout_of_range = "maybe"\n', "out_of_range"),
            ("[genshin]\ntranspose = 99\n", "transpose"),
            ("[midi]\nchannel = 42\n", "channel"),
            ("[midi]\nretry_seconds = 0\n", "retry_seconds"),
            ("[midi]\nretry_seconds = -1\n", "retry_seconds"),
            ("[midi]\nwatchdog_seconds = 0\n", "watchdog_seconds"),
            ("[midi]\nsubstring = false\n", "exact"),
        ],
    )
    def test_invalid_values_are_rejected(self, tmp_path: Path, body: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            load_config(write(tmp_path, body))

    def test_exact_match_with_a_device_is_allowed(self, tmp_path: Path) -> None:
        config = load_config(write(tmp_path, '[midi]\ndevice = "Casio"\nsubstring = false\n'))
        assert config.midi.device == "Casio"


class TestWriteDefault:
    def test_writes_a_loadable_file(self, tmp_path: Path) -> None:
        destination = tmp_path / "midiator.toml"
        write_default_config(destination)
        assert load_config(destination).preset == "genshin"

    def test_refuses_to_overwrite(self, tmp_path: Path) -> None:
        destination = tmp_path / "midiator.toml"
        write_default_config(destination)
        with pytest.raises(FileExistsError):
            write_default_config(destination)
