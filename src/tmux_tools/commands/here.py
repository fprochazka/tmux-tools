"""``tmux-here``: attach to the tmux session that runs in the current directory."""

from __future__ import annotations

import os
import re

import typer
from rich.text import Text

from .. import tmux
from ..console import ask, error, line, warn
from ..entrypoint import bootstrap
from ..naming import propose_names
from ..tmux import Pane

# bash tests a selection with ^[0-9]+$; str.isdigit() would also accept "²" and then fail on int()
_NUMBER = re.compile(r"[0-9]+")

app = typer.Typer(add_completion=False, rich_markup_mode=None)


@app.command()
def here() -> None:
    """Attach to a tmux session that runs in the current directory.

    A session matches when any of its panes has a current path equal to the current directory
    or below it. With one match, tmux-here attaches to it. With several matches, it prints a
    numbered list and waits for a number.

    With no match, tmux-here proposes session names and creates the session. A proposal is the
    repository the git remote points at, with the checked out branch after it. Outside a git
    repository it asks for a name without a proposal.

    Inside tmux it switches the client instead of attaching.
    """
    tmux.require_installed()

    # os.getcwd() is already the physical path, the kernel's symlink-free one, so it needs no
    # realpath; the logical path is the spelling the shell got there by.
    physical = os.getcwd()
    logical = _logical_cwd(physical)
    current = tmux.current_session()

    matches = tmux.sessions_in(tmux.list_panes(), (physical, logical)) if tmux.server_running() else []

    if len(matches) == 1:
        _attach(matches[0].session, current)
    elif len(matches) > 1:
        _pick_session(matches, physical, current)
    else:
        _offer_new_session(physical)


def _logical_cwd(physical: str) -> str:
    """The shell's ``$PWD``, which keeps the symlinks ``os.getcwd()`` has already resolved.

    Bash rejects an inherited ``PWD`` that does not point at the current directory, and so
    does this: a stale value left in the environment would otherwise match the wrong panes.
    """
    pwd = os.environ.get("PWD", "")
    if pwd.startswith("/") and os.path.realpath(pwd) == physical:
        return pwd
    return physical


def _attach(target: str, current: str) -> None:
    if target == current:
        warn(f"Already in session '{target}'.")
        return

    if tmux.inside_tmux():
        tmux.switch_client(target)
    else:
        tmux.exec_attach(target)


def _pick_session(matches: list[Pane], root: str, current: str) -> None:
    line()
    line(Text(f"Sessions in {root}:", style="bold"))
    line()

    for index, pane in enumerate(matches, start=1):
        row = Text(f"  {index:2d}) ")
        row.append(pane.session, style="green")
        if pane.session == current:
            row.append(" ")
            row.append("[current]", style="yellow")
        line(row)
        line(Text("      ").append(pane.path, style="dim"))

    line()

    while True:
        answer = ask("Enter number to attach, or a new session name (Ctrl+C to cancel): ")
        if not answer:
            continue

        if _NUMBER.fullmatch(answer):
            index = int(answer) - 1
            if index < 0 or index >= len(matches):
                # Out of range only costs another turn around the loop; a typo is not fatal here
                error(f"Selection out of range: {answer}")
                continue
            _attach(matches[index].session, current)
            return

        # A directory with three sessions in it is a directory that can want a fourth
        if _create_session(answer, root):
            return


def _offer_new_session(root: str) -> None:
    proposals = propose_names(root)

    line()
    warn(f"No tmux session runs in {root}.")
    line()

    if proposals:
        for index, name in enumerate(proposals, start=1):
            line(Text(f"  {index:2d}) ").append(name, style="green"))
        line()

    while True:
        if proposals:
            answer = ask("Enter number or a new session name (Ctrl+C to cancel): ")
        else:
            answer = ask("Enter a new session name (Ctrl+C to cancel): ")

        if not answer:
            continue

        name = answer
        if proposals and _NUMBER.fullmatch(answer):
            index = int(answer) - 1
            if index < 0 or index >= len(proposals):
                # Out of range only costs another turn around the loop; a typo is not fatal here
                error(f"Selection out of range: {answer}")
                continue
            name = proposals[index]

        if _create_session(name, root):
            return


def _create_session(name: str, path: str) -> bool:
    """Start a session called ``name`` in ``path`` and go to it, and say whether it happened.

    False means another session already holds the name, and the caller is expected to ask
    again. Both prompts create a session through here, so neither can refuse a name the other
    would have taken.
    """
    if tmux.session_exists(name):
        error(f"Session '{name}' already exists elsewhere. Pick another name.")
        return False

    if tmux.inside_tmux():
        tmux.new_session_detached(name, path)
        tmux.switch_client(name)
    else:
        tmux.exec_new_session(name, path)
    return True


def main() -> None:
    bootstrap()
    app()
