"""Command-line entry point: ``midiator`` / ``python -m midiator``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import mido

from midiator.config import DEFAULT_CONFIG_NAME, Config, load_config, write_default_config

logger = logging.getLogger("midiator")


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="midiator",
        description="Play a MIDI keyboard into Windows as keyboard and mouse input, "
        "including Genshin Impact's 21-key instrument layout.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  midiator                      start with midiator.toml\n"
            "  midiator --init               write a commented default midiator.toml\n"
            "  midiator --list-devices       show every MIDI input Windows reports\n"
            "  midiator --device Casio       pick the device on the command line\n"
            "  midiator --transpose -2       shift every note down two semitones\n"
            "  midiator --preview 60 62 64   show the mapping without sending input\n"
        ),
    )
    parser.add_argument(
        "-c", "--config", type=Path, default=None, help=f"path to a config file (default: {DEFAULT_CONFIG_NAME})"
    )
    parser.add_argument("--init", action="store_true", help="write a default config file and exit")
    parser.add_argument("--list-devices", action="store_true", help="list MIDI input devices and exit")
    parser.add_argument("--preset", choices=("genshin", "generic"), default=None, help="override the configured preset")
    parser.add_argument("--device", default=None, help="override the configured MIDI device name")
    parser.add_argument("--channel", type=int, default=None, help="override the configured MIDI channel (0-15)")
    parser.add_argument("--transpose", type=int, default=None, help="override the Genshin transpose in semitones")
    parser.add_argument(
        "--key-mode", choices=("scancode", "virtualkey"), default=None, help="override how keys are sent"
    )
    parser.add_argument(
        "--no-reconnect",
        action="store_true",
        help="exit instead of waiting for the MIDI device to appear or come back",
    )
    parser.add_argument(
        "--preview", type=int, nargs="+", metavar="NOTE", help="print the mapping for MIDI notes and exit"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="only log warnings and errors")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug detail")
    parser.add_argument("--dry-run", action="store_true", help="log input events instead of sending them")
    return parser


def _configure_logging(quiet: bool, verbose: bool) -> None:
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout, force=True)


def _apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.preset is not None:
        config.preset = args.preset
    if args.device is not None:
        config.midi.device = args.device
    if args.channel is not None:
        config.midi.channel = args.channel
    if args.transpose is not None:
        config.genshin.transpose = args.transpose
    if args.key_mode is not None:
        config.windows.key_mode = args.key_mode
    if args.no_reconnect:
        config.midi.reconnect = False
    return config


def _list_devices() -> int:
    from midiator.midi import MidiDeviceError, list_input_names

    try:
        names = list_input_names()
    except MidiDeviceError as error:
        logger.error("%s", error)
        return 1
    if not names:
        logger.info("No MIDI input devices found. Is the keyboard plugged in and powered on?")
        return 1
    logger.info("MIDI input devices:")
    for name in names:
        logger.info("  %s", name)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.quiet, args.verbose)

    if args.init:
        destination = args.config or Path.cwd() / DEFAULT_CONFIG_NAME
        try:
            written = write_default_config(destination)
        except FileExistsError as error:
            logger.error("%s", error)
            return 1
        logger.info("Wrote %s", written)
        return 0

    # Imported late so that --list-devices, --init and --preview work without
    # touching the Windows input layer.
    from midiator.engine import Midiator, preview
    from midiator.windows.keyboard import WindowsKeySender
    from midiator.windows.mouse import WindowsMouseSender

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as error:
        logger.error("%s", error)
        return 2
    config = _apply_overrides(config, args)

    if config.path is not None:
        logger.info("Using configuration from %s", config.path)
    if args.dry_run:
        logger.info("Dry run: no input will be sent.")

    if args.list_devices:
        return _list_devices()

    keys = WindowsKeySender(mode=config.windows.key_mode, dry_run=args.dry_run)
    mouse = WindowsMouseSender(dry_run=args.dry_run)

    if args.preview:
        for line in preview(list(args.preview), config):
            logger.info("%s", line)
        return 0

    engine = Midiator(config, keys, mouse)

    from midiator.midi import MidiDeviceError, MidiInput, MidiSupervisor, open_input

    def open_port() -> MidiInput:
        return open_input(config.midi.device, substring=config.midi.substring)

    def handle(message: mido.Message) -> None:
        transition = engine.notes.update(message)
        if transition is not None:
            engine.handle(transition)

    def announce_waiting(error: MidiDeviceError) -> None:
        logger.info("%s", error)
        if "Available inputs" in str(error):
            logger.info("Waiting for the MIDI device; plug it in and playing will start automatically.")

    def on_disconnect(error: BaseException | None) -> None:
        # Release everything before looking for the device again, so no key stays
        # stuck down in the game while the keyboard is unplugged.
        engine.all_off()
        logger.info("Releasing every held key and waiting for the device to come back.")

    supervisor = MidiSupervisor(
        open_port,
        retry_seconds=config.midi.retry_seconds,
        watchdog_seconds=config.midi.watchdog_seconds,
        reconnect=config.midi.reconnect,
    )

    engine.describe_setup()
    engine.start_mouse_thread()
    if config.midi.reconnect:
        logger.info("Watching for the MIDI device; unplugging it is safe, MIDIator will wait and reconnect.")
    logger.info("Handling MIDI events. Press Ctrl+C to stop.")

    try:
        supervisor.run(handle, on_disconnect=on_disconnect, on_waiting=announce_waiting)
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        supervisor.stop()
    except MidiDeviceError as error:
        logger.error("%s", error)
        return 1
    finally:
        engine.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
