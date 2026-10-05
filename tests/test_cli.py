"""Tests for the CLI, including the paths that must not touch Windows input."""

from __future__ import annotations

from pathlib import Path

import pytest

from midiator.__main__ import build_parser, main
from midiator.config import load_config


class TestInit:
    def test_init_writes_config_and_exits_zero(self, tmp_path: Path) -> None:
        destination = tmp_path / "midiator.toml"
        assert main(["--init", "-c", str(destination)]) == 0
        assert destination.is_file()
        assert load_config(destination).preset == "genshin"

    def test_init_refuses_to_clobber(self, tmp_path: Path) -> None:
        destination = tmp_path / "midiator.toml"
        destination.write_text("preset = 'genshin'\n", encoding="utf-8")
        assert main(["--init", "-c", str(destination)]) == 1


class TestPreview:
    def test_preview_prints_mapping_without_config(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = tmp_path / "midiator.toml"
        config.write_text("preset = 'genshin'\n", encoding="utf-8")
        assert main(["--preview", "60", "61", "84", "-c", str(config)]) == 0
        output = capsys.readouterr().out
        assert "C4" in output
        assert "C#4" in output
        assert "key A" in output  # C4
        assert "key Q" in output  # C6 folds to C5

    def test_preview_honours_transpose(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = tmp_path / "midiator.toml"
        config.write_text("preset = 'genshin'\n[genshin]\ntranspose = 2\n", encoding="utf-8")
        assert main(["--preview", "58", "-c", str(config)]) == 0
        assert "key A" in capsys.readouterr().out


class TestBadConfig:
    def test_missing_explicit_config_exits_two(self, tmp_path: Path) -> None:
        assert main(["--preview", "60", "-c", str(tmp_path / "absent.toml")]) == 2

    def test_invalid_value_exits_two(self, tmp_path: Path) -> None:
        config = tmp_path / "midiator.toml"
        config.write_text("[genshin]\ntranspose = 500\n", encoding="utf-8")
        assert main(["--preview", "60", "-c", str(config)]) == 2


class TestParser:
    def test_defaults_are_all_none_so_overrides_are_opt_in(self) -> None:
        args = build_parser().parse_args([])
        assert args.preset is None
        assert args.device is None
        assert args.transpose is None
        assert args.key_mode is None
        assert args.init is False
        assert args.dry_run is False

    def test_preview_takes_many_notes(self) -> None:
        args = build_parser().parse_args(["--preview", "60", "62", "64"])
        assert args.preview == [60, 62, 64]

    def test_overrides_are_applied_to_the_config(self, tmp_path: Path) -> None:
        config = tmp_path / "midiator.toml"
        config.write_text("preset = 'genshin'\n[midi]\nchannel = 0\n", encoding="utf-8")
        assert main(["--preview", "60", "--transpose", "3", "--device", "Casio", "-c", str(config)]) == 0

    def test_invalid_key_mode_is_rejected_by_the_parser(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--key-mode", "telepathy"])
