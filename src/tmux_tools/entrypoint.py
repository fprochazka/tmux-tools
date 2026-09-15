"""Bootstrap shared by every console script in this package.

Two things a command needs before it writes anything: output that reaches the terminal as it
is produced, and a Ctrl+C that ends the process the way the prompts promise it will.
"""

from __future__ import annotations

import os
import signal
import sys
from types import FrameType
from typing import NoReturn


def bootstrap() -> None:
    """Prepare the process. Call this first from every command's ``main()``."""
    _configure_unbuffered_output()
    _exit_quietly_on_interrupt()


def _configure_unbuffered_output() -> None:
    """Make stdout and stderr flush on every line.

    ``PYTHONUNBUFFERED`` is read once at interpreter startup, so setting it in ``os.environ``
    from inside the process changes nothing, and the console script uv generates leaves no
    place to pass ``-u``. Reconfiguring the streams is the only lever left.

    ``write_through`` only bypasses the text layer. The ``BufferedWriter`` underneath still
    holds bytes until something flushes it, and ``line_buffering`` is what forces that flush
    on each newline. A prompt that ends without a newline still needs its own flush.
    """
    sys.stdout.reconfigure(line_buffering=True, write_through=True)
    sys.stderr.reconfigure(line_buffering=True, write_through=True)


def _exit_quietly_on_interrupt() -> None:
    """Turn Ctrl+C into a plain exit with status 130.

    Click catches ``KeyboardInterrupt`` and answers with ``Aborted!`` on stderr and status 1.
    Raising ``SystemExit`` from the handler instead goes past click untouched and still runs
    the interpreter's own shutdown, so the terminal is left in the state it was found in.
    """

    def handler(signum: int, frame: FrameType | None) -> NoReturn:
        raise SystemExit(130)

    signal.signal(signal.SIGINT, handler)


def exec_process(argv: list[str]) -> NoReturn:
    """Replace this process with ``argv``, handing over the terminal.

    Deliberately not ``subprocess.run``. A program that owns the terminal, such as
    ``tmux attach``, has to own the process too, instead of running as a child under a Python
    parent that would sit in the middle of the session for as long as the session lasts.

    Exec never returns on success, and it drops buffered output along with ``atexit`` and
    ``finally`` handlers, so flush first. It raises ``OSError`` when the program is missing.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(argv[0], argv)
