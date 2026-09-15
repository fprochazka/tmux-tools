"""``tmux-there``: attach to a tmux session on any machine on the tailnet."""

from __future__ import annotations

import re
import shlex
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass

import typer
from rich.live import Live
from rich.text import Text

from .. import tailnet, tmux
from ..config import load_config
from ..console import ask, die, line, out, warn
from ..entrypoint import bootstrap, exec_process
from ..tailnet import Device, Probe
from ..tmux import Session

# bash tests a selection with ^[0-9]+$; str.isdigit() would also accept "²" and then fail on int()
_NUMBER = re.compile(r"[0-9]+")

# How long the canary keeps the tailnet to itself before the rest of the probes follow.
_CANARY_WINDOW = 4.0

# Wall clock for the whole scan. A peer can answer the coordination server and not answer ssh.
_SCAN_DEADLINE = 15.0

# How often the waiting loop looks at the probes it is waiting for.
_POLL = 0.2

app = typer.Typer(add_completion=False, rich_markup_mode=None)


@dataclass(frozen=True)
class Pick:
    """One numbered line of the picker: a session and the device it runs on."""

    device: Device
    session: Session


@app.command()
def there() -> None:
    """Attach to a tmux session on any machine on the tailnet.

    tmux-there lists the sessions of every online device on your Tailscale tailnet, this one
    included, numbered in one sequence across all of them, and attaches to the one you pick.
    A session on another machine is reached over ssh, a local one the way tmux-here reaches it.

    Devices that answer with nothing are not listed at all. No tmux, no running tmux server and
    a refused connection all mean the same thing here, and none of them is worth a message.

    Without a tailnet to read, this machine is the whole list and its sessions still show up.
    A stopped tailscaled is a reason to see fewer machines, not a reason to see none.

    An optional config file at ~/.config/fprochazka-tmux-tools/config.toml narrows the devices
    to scan, with devices.only or devices.skip. Without a config file every online device is
    scanned, which is the intended way to run this.
    """
    tmux.require_installed()

    config = load_config()
    status = tailnet.status()
    devices = tailnet.online_devices(status)
    for name in tailnet.unmatched_names(devices, config):
        warn(f"The config file asks for '{name}', and no device found matches it.")

    current = tmux.current_session()
    found = _scan(tailnet.select_devices(devices, config))
    picks = flatten(found)
    if not picks:
        die("No tmux session runs anywhere on the tailnet." if status else "No tmux session runs on this machine.")

    _attach(_choose(found, picks, current), current)


def flatten(found: list[tuple[Device, list[Session]]]) -> list[Pick]:
    """The sessions of every device in one list, in the order the picker prints them.

    The numbering runs over this list rather than per device, so answering the prompt is one
    number and not a device and a number.
    """
    return [Pick(device, session) for device, sessions in found for session in sessions]


def _scan(devices: list[Device]) -> list[tuple[Device, list[Session]]]:
    """Every selected device and the sessions on it, with the silent devices left out.

    This machine is read through the tmux binary, with no ssh in the way. The rest are probed
    over ssh, the canary alone first so that at most one authentication check is outstanding.
    """
    sessions: dict[Device, list[Session]] = {}

    for device in devices:
        if device.is_self:
            sessions[device] = tmux.list_sessions()

    for probe in _probe_all([device for device in devices if not device.is_self]):
        sessions[probe.device] = probe.sessions

    return [(device, sessions[device]) for device in devices if sessions.get(device)]


def _probe_all(devices: list[Device]) -> list[Probe]:
    """Probe the devices: the canary on its own first, then everything else at once.

    Nothing here is ever this machine, so this is also the first point that needs an ssh client
    and the only place worth asking for one.
    """
    if not devices:
        return []

    tailnet.require_ssh()
    canary = tailnet.choose_canary(devices)
    pool = ThreadPoolExecutor(max_workers=len(devices))
    running: dict[Probe, Future[None]] = {}

    try:
        if canary is not None:
            probe = Probe(canary)
            running[probe] = pool.submit(probe.run)
            _wait_for_canary(probe, running[probe])

        for device in devices:
            if device is not canary:
                probe = Probe(device)
                running[probe] = pool.submit(probe.run)

        _wait_for_probes(running, time.monotonic() + _SCAN_DEADLINE)
        for future in running.values():
            # A probe that failed for a reason of its own is a bug, not a quiet host
            future.result()
        return list(running)
    finally:
        # Ctrl+C arrives here with connections still open. Killing them is what lets the pool
        # shut down now instead of joining threads that sit blocked on ssh for another minute.
        for probe in running:
            probe.stop()
        pool.shutdown(wait=True)


def _wait_for_canary(probe: Probe, future: Future[None]) -> None:
    """Give the canary its head start, and wait for the user when it asks for a browser login.

    The wait is short and ends as soon as the canary has anything to say. An authentication URL
    stops the clock, because starting the other probes now would put the same question to every
    host on the tailnet. A listing, or nothing at all, and the scan carries on with the canary
    still running, so a canary on an unreachable host holds nothing up.
    """
    deadline = time.monotonic() + _CANARY_WINDOW
    while time.monotonic() < deadline and not future.done() and not probe.waiting_for_login:
        time.sleep(_POLL)

    if probe.waiting_for_login:
        _wait_for_probes({probe: future}, None)


def _wait_for_probes(running: dict[Probe, Future[None]], deadline: float | None) -> None:
    """Wait for the probes, naming the hosts still outstanding while they run.

    A probe that has printed an authentication URL is waiting on a human and keeps no deadline.
    The others are killed when the clock runs out, because a peer that answers the coordination
    server is not a peer that answers ssh from wherever you happen to be.
    """
    if all(future.done() for future in running.values()):
        # Nothing to wait for and so nothing to say about it
        return

    with _progress() as show:
        while True:
            pending = [probe for probe, future in running.items() if not future.done()]
            if not pending:
                return

            if deadline is not None and time.monotonic() >= deadline:
                for probe in pending:
                    if not probe.waiting_for_login:
                        probe.stop()
                deadline = None

            show(pending)
            time.sleep(_POLL)


@contextmanager
def _progress() -> Iterator[Callable[[list[Probe]], None]]:
    """A single line naming the hosts still being read, gone as soon as the scan is done.

    It is here to tell a scan that is waiting from a scan that has hung, and for nothing else.
    """
    with Live(console=out, transient=True, auto_refresh=False) as live:

        def show(probes: list[Probe]) -> None:
            names = ", ".join(probe.device.name for probe in probes)
            live.update(Text(f"Reading {names}", style="dim"), refresh=True)

        yield show


def _choose(found: list[tuple[Device, list[Session]]], picks: list[Pick], current: str) -> Pick:
    line()

    index = 0
    for device, sessions in found:
        header = Text(device.name, style="bold")
        if device.is_self:
            header.append(" (local)", style="dim")
        line(header)

        for session in sessions:
            index += 1
            row = Text(f"  {index:2d}) ")
            row.append(session.name, style="green")
            if device.is_self and session.name == current:
                row.append(" ")
                row.append("[current]", style="yellow")
            line(row)
            line(Text("      ").append(session.path, style="dim"))

        line()

    selection = ask("Enter number to attach (Ctrl+C to cancel): ")

    if not _NUMBER.fullmatch(selection):
        die(f"Invalid selection: {selection}")

    number = int(selection) - 1
    if number < 0 or number >= len(picks):
        die(f"Selection out of range: {selection}")

    return picks[number]


def _attach(pick: Pick, current: str) -> None:
    """Hand the terminal over to the session, replacing this process on the way out.

    The attach carries no ``-d``. A machine you are away from may well have a client of its own
    on the session, open for weeks, and reaching that session from the road is no reason to
    throw the other client off it.
    """
    if pick.device.is_self:
        _attach_locally(pick.session.name, current)
        return

    # ssh joins everything after the host name into one string for a login shell on the other
    # side, where a session name with a space or a quote in it would come apart. Locally there
    # is no shell between the argv and tmux, which is why tmux-here needs none of this.
    name = shlex.quote(pick.session.name)
    try:
        exec_process(["ssh", "-t", pick.device.host, "tmux", "attach", "-t", name])
    except OSError:
        die("ssh is not installed.")


def _attach_locally(name: str, current: str) -> None:
    if name == current:
        warn(f"Already in session '{name}'.")
        return

    if tmux.inside_tmux():
        tmux.switch_client(name)
    else:
        tmux.exec_attach(name)


def main() -> None:
    bootstrap()
    app()
