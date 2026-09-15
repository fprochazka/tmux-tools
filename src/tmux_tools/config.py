"""The optional config file, which so far only narrows the tailnet devices worth scanning."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .console import die

# The directory carries the author's name because "tmux-tools" is a name another project is
# likely to want in ~/.config one day. Nothing else in the package reads a config file.
CONFIG_DIR = "fprochazka-tmux-tools"
CONFIG_FILE = "config.toml"


@dataclass(frozen=True)
class Config:
    """What the config file says, with the defaults that apply when there is no file."""

    only: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()


def config_path() -> Path:
    """Where the config file is looked for, whether or not anything is there."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / CONFIG_DIR / CONFIG_FILE


def load_config(path: Path | None = None) -> Config:
    """Read the config file. No file is the normal case and gives the defaults.

    The commands have to keep working on a machine that was never set up, so absence is not a
    failure. A file that is there and cannot be understood is, because the user meant it.
    """
    path = path or config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Config()
    except OSError as problem:
        die(f"Cannot read the config file {path}: {problem.strerror}")

    try:
        parsed = tomllib.loads(raw.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as problem:
        die(f"The config file {path} is not valid TOML: {problem}")

    devices = parsed.get("devices", {})
    if not isinstance(devices, dict):
        die(f"The config file {path} needs [devices] to be a table.")

    return Config(only=_names(path, devices, "only"), skip=_names(path, devices, "skip"))


def _names(path: Path, devices: dict, key: str) -> tuple[str, ...]:
    """One list of device names from the ``[devices]`` table, empty when it is not there."""
    value = devices.get(key, [])
    if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
        die(f"The config file {path} needs devices.{key} to be a list of device names.")
    return tuple(value)
