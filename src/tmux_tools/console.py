"""Rich consoles for stdout and stderr, and the small output helpers built on them."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
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
    """A message about the run rather than its result, kept off stdout so a pipe gets only the result."""
    err.print(Text(message, style="yellow"))


def error(message: str) -> None:
    out.print(Text(message, style="red"))


def die(message: str) -> NoReturn:
    err.print(Text(message, style="red"))
    raise SystemExit(1)


@contextmanager
def silenced() -> Iterator[None]:
    """Nothing reaches stderr while this is open, from this process or from the ones it starts.

    For a run whose stdout is read by a shell completion while the user looks at the prompt, where
    any line on stderr lands in the middle of the command line being typed. The console is told
    to keep quiet, and file descriptor 2 points at /dev/null for the child processes, tailscale
    among them, that write there on their own.
    """
    err.quiet = True
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(devnull)
        err.quiet = False


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
