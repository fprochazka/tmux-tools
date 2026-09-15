"""Everything this package asks of the tmux binary, plus the pane matching built on it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import NoReturn

from .console import die
from .entrypoint import exec_process

_PANE_FORMAT = "#{session_name}\t#{pane_current_path}"


@dataclass(frozen=True)
class Pane:
    """One line of ``tmux list-panes``: the session a pane belongs to and its current path."""

    session: str
    path: str


def _run(args: list[str], *, capture: bool = True, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    """Run tmux. ``capture`` keeps stdout, ``quiet`` drops stderr the way ``2> /dev/null`` does."""
    return subprocess.run(
        ["tmux", *args],
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.DEVNULL if quiet else None,
        text=True,
        check=False,
    )


def _run_or_exit(args: list[str], *, capture: bool = True) -> str:
    """Run tmux and leave with its exit code if it fails, letting its own message through."""
    result = _run(args, capture=capture)
    if result.returncode != 0:
        sys.exit(result.returncode)
    return result.stdout.rstrip("\n") if capture else ""


def require_installed() -> None:
    if shutil.which("tmux") is None:
        die("tmux is not installed.")


def inside_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def current_session() -> str:
    """Name of the session this process runs in, empty outside tmux."""
    if not inside_tmux():
        return ""
    return _run_or_exit(["display-message", "-p", "#{session_name}"])


def server_running() -> bool:
    """False when no tmux server is up, which means no sessions rather than a failure."""
    return _run(["has-session"], quiet=True).returncode == 0


def session_exists(name: str) -> bool:
    # The "=" prefix asks tmux for an exact name, without its usual prefix matching.
    return _run(["has-session", f"-t={name}"], quiet=True).returncode == 0


def list_panes() -> list[Pane]:
    """Every pane of every session, in the order tmux reports them."""
    panes: list[Pane] = []
    for raw in _run(["list-panes", "-a", "-F", _PANE_FORMAT]).stdout.splitlines():
        session, _, path = raw.partition("\t")
        panes.append(Pane(session=session, path=path))
    return panes


def is_within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` itself or sits below it.

    The comparison is segment aware, so ``/foo/bar`` does not count as below ``/foo/ba``.
    """
    return path == root or path.startswith(root + "/")


def sessions_in(panes: list[Pane], roots: tuple[str, ...]) -> list[Pane]:
    """Sessions with at least one pane under any of ``roots``, one entry each.

    A session is represented by its first pane that matches, not by its first pane overall,
    and the result keeps the order tmux listed the panes in.
    """
    matches: list[Pane] = []
    seen: set[str] = set()
    for pane in panes:
        if not pane.session or pane.session in seen:
            continue
        if any(is_within(pane.path, root) for root in roots):
            seen.add(pane.session)
            matches.append(pane)
    return matches


def switch_client(name: str) -> None:
    _run_or_exit(["switch-client", "-t", name], capture=False)


def new_session_detached(name: str, path: str) -> None:
    _run_or_exit(["new-session", "-d", "-s", name, "-c", path], capture=False)


def exec_attach(name: str) -> NoReturn:
    _exec(["tmux", "attach", "-t", name])


def exec_new_session(name: str, path: str) -> NoReturn:
    _exec(["tmux", "new-session", "-s", name, "-c", path])


def _exec(argv: list[str]) -> NoReturn:
    try:
        exec_process(argv)
    except OSError:
        die("tmux is not installed.")
