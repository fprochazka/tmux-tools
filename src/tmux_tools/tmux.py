"""Everything this package asks of the tmux binary, plus the pane matching built on it."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import NoReturn

from .console import die
from .entrypoint import exec_process

_PANE_FORMAT = "#{session_name}\t#{pane_current_path}"
_SESSION_FORMAT = "#{session_name}\t#{session_path}"

# What a tmux command runs behind. Nothing for the tmux binary on this machine, ssh and its
# options for a tmux on another one.
Prefix = tuple[str, ...]
LOCAL: Prefix = ()


@dataclass(frozen=True)
class Pane:
    """One line of ``tmux list-panes``: the session a pane belongs to and its current path."""

    session: str
    path: str


@dataclass(frozen=True)
class Session:
    """One line of ``tmux list-sessions``: a session and the directory it was started in."""

    name: str
    path: str


def command(args: list[str], prefix: Prefix = LOCAL) -> list[str]:
    """The argv that runs ``tmux args``, on this machine or behind ``prefix``.

    ssh takes everything after the host name, joins it with spaces and hands the result to a
    login shell on the other side. Arguments that mean something to a shell, such as the tab
    inside a format string, arrive as two words unless they are quoted for it first.
    """
    if not prefix:
        return ["tmux", *args]
    return [*prefix, shlex.join(["tmux", *args])]


def _run(
    args: list[str], *, prefix: Prefix = LOCAL, capture: bool = True, quiet: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run tmux. ``capture`` keeps stdout, ``quiet`` drops stderr the way ``2> /dev/null`` does."""
    return subprocess.run(
        command(args, prefix),
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


def list_panes(prefix: Prefix = LOCAL) -> list[Pane]:
    """Every pane of every session, in the order tmux reports them."""
    panes: list[Pane] = []
    for raw in _run(["list-panes", "-a", "-F", _PANE_FORMAT], prefix=prefix).stdout.splitlines():
        fields = _fields(raw)
        if fields is not None:
            panes.append(Pane(*fields))
    return panes


def list_sessions_command(prefix: Prefix = LOCAL) -> list[str]:
    """The argv of a session listing, for a caller that has to watch the output as it arrives."""
    return command(["list-sessions", "-F", _SESSION_FORMAT], prefix)


def list_sessions(prefix: Prefix = LOCAL) -> list[Session]:
    """Every session, in the order tmux reports them, empty when no server is running.

    No server is not a failure here. tmux says so on stderr and exits non-zero, and both are
    dropped, because a machine with nothing running is a machine with no sessions to offer.
    """
    sessions: list[Session] = []
    for raw in _run(["list-sessions", "-F", _SESSION_FORMAT], prefix=prefix, quiet=True).stdout.splitlines():
        session = parse_session(raw)
        if session is not None:
            sessions.append(session)
    return sessions


def parse_session(raw: str) -> Session | None:
    """One line of ``tmux list-sessions``, or ``None`` when the line is not one."""
    fields = _fields(raw)
    return Session(*fields) if fields is not None else None


def _fields(raw: str) -> tuple[str, str] | None:
    """Split a ``#{name}\t#{path}`` line, or ``None`` when the line does not have that shape.

    A listing read over ssh arrives mixed with everything else the connection printed, so a
    line counts only when the tab is there and something stands in front of it.
    """
    name, tab, path = raw.partition("\t")
    if not tab or not name:
        return None
    return name, path


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
