"""Rich consoles for stdout and stderr, and the small output helpers built on them."""

from __future__ import annotations

import sys
from typing import NoReturn

from rich.console import Console
from rich.text import Text

# Colors follow stdout for both streams: a piped stdout means no colors anywhere, even when
# stderr is still a terminal. Passing the answer explicitly keeps rich from deciding per stream.
_COLORS = sys.stdout.isatty()

# soft_wrap keeps long session paths on one line. highlight and markup are off because
# session names and paths are arbitrary text: a name with brackets must print as typed.
out = Console(force_terminal=_COLORS, highlight=False, markup=False, soft_wrap=True)
err = Console(stderr=True, force_terminal=_COLORS, highlight=False, markup=False, soft_wrap=True)


def line(text: Text | str = "") -> None:
    out.print(text)


def warn(message: str) -> None:
    out.print(Text(message, style="yellow"))


def error(message: str) -> None:
    out.print(Text(message, style="red"))


def die(message: str) -> NoReturn:
    err.print(Text(message, style="red"))
    raise SystemExit(1)


def ask(question: str) -> str:
    """Print a prompt, read one line, and strip it the way bash ``read -r`` does.

    ``read -r`` drops leading and trailing IFS whitespace, which is spaces and tabs, and
    nothing else. Python's ``str.strip()`` would also eat a trailing carriage return.
    """
    out.print(question, end="")
    out.file.flush()
    try:
        return input().strip(" \t")
    except EOFError:
        die("No selection.")
