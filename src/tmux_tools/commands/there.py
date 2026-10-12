"""``tmux-there``: attach to a tmux session on any machine on the tailnet."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated

import typer
from rich.live import Live
from rich.text import Text

from .. import tailnet, tmux
from ..config import config_path, load_config
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

    @property
    def label(self) -> str:
        return label(self.session.name, self.device.name)


def label(session: str, device: str) -> str:
    """``session@device``, the one name a session goes by in every listing and in ``attach``."""
    return f"{session}@{device}"


def parse_target(target: str) -> tuple[str, str]:
    """Split ``session@device`` into the session name and the device name.

    The split is on the last ``@``. A host name cannot hold one, and a session name can.
    """
    session, at, device = target.rpartition("@")
    if not at or not session or not device:
        die(f"Expected SESSION@DEVICE, got '{target}'.")
    return session, device


@app.callback(invoke_without_command=True)
def there(ctx: typer.Context) -> None:
    """Attach to a tmux session on any machine on the tailnet.

    Without a command, tmux-there lists the sessions of every online device on your Tailscale
    tailnet, this one included, numbered in one sequence across all of them, and attaches to the
    one you pick by its number or its session@device name. A session on another machine is
    reached over ssh, a local one the way tmux-here reaches it.

    Devices with no sessions are not listed. A device that cannot be listed, because Tailscale
    reports it offline, ssh cannot connect or does not answer in time, is named on stderr with
    the reason. No tmux and no running tmux server on a device that answered need no message.

    Without a tailnet to read, this machine is the whole list and its sessions still show up.
    A stopped tailscaled is a reason to see fewer machines, not a reason to see none.

    An optional config file at ~/.config/fprochazka-tmux-tools/config.toml narrows the devices
    to scan, with devices.only or devices.skip. Without a config file every online device is
    scanned, which is the intended way to run this.
    """
    if ctx.invoked_subcommand is not None:
        return

    tmux.require_installed()

    current = tmux.current_session()
    found, tailnet_read = _collect()
    picks = flatten(found)
    if not picks:
        where = "anywhere on the tailnet" if tailnet_read else "on this machine"
        die(f"No tmux session runs {where}.")

    pick = _choose(found, picks, current)
    _attach(pick.device, pick.session.name, current)


@app.command("ls")
def list_all() -> None:
    """List the sessions on every device, numbered and grouped the way the picker shows them.

    This is the picker without its prompt, so a number read here is the number the picker
    would take for the same scan. Messages about the scan go to stderr, and no session
    anywhere prints nothing.
    """
    tmux.require_installed()

    current = tmux.current_session()
    found, _ = _collect()
    if found:
        _render(found, current)


@app.command("attach")
def attach(
    target: Annotated[str, typer.Argument(metavar="SESSION@DEVICE", help="A name as tmux-there ls prints it.")],
) -> None:
    """Attach to the session named session@device, without asking.

    The session has to be one that tmux-there ls would print. The device is looked up by its
    host name or its MagicDNS name, the config file narrows it the same way it narrows the
    listing, and only that one device is read to find the session. Nothing is ever created.
    """
    tmux.require_installed()

    name, device_name = parse_target(target)
    device = _resolve_device(device_name)
    sessions = _sessions_on(device)
    if not sessions:
        die(f"No tmux session runs on {device.name}.")
    if all(session.name != name for session in sessions):
        die(f"No session '{name}' on {device.name}. tmux-there ls lists the sessions there are.")

    _attach(device, name, tmux.current_session())


def flatten(found: list[tuple[Device, list[Session]]]) -> list[Pick]:
    """The sessions of every device in one list, in the order the picker prints them.

    The numbering runs over this list rather than per device, so answering the prompt is one
    number and not a device and a number.
    """
    return [Pick(device, session) for device, sessions in found for session in sessions]


def _collect() -> tuple[list[tuple[Device, list[Session]]], bool]:
    """Scan the devices the config file leaves in, and say whether there was a tailnet to read."""
    config = load_config()
    status = tailnet.status()
    devices = tailnet.online_devices(status)
    for name in tailnet.unmatched_names(devices, config):
        reason = tailnet.why_missing(status, name)
        if reason is None:
            warn(f"The config file asks for '{name}', and no device found matches it.")
        else:
            warn(f"{name}: {reason}. Its sessions are not listed.")

    return _scan(tailnet.select_devices(devices, config)), bool(status)


def _resolve_device(name: str) -> Device:
    """The device ``attach`` was asked for, if the listing would have shown it, or a reason it would not.

    A tailnet that cannot be read still leaves this machine, the same as it does for the listing,
    so only a name that is not this machine fails on it.
    """
    config = load_config()
    try:
        status = tailnet.read()
        problem = ""
    except tailnet.TailnetUnavailable as unavailable:
        status, problem = {}, str(unavailable)

    devices = tailnet.online_devices(status)
    device = tailnet.find_device(devices, name)
    if device is None:
        if problem:
            die(f"Cannot read the tailnet to find '{name}'. {problem}")
        if not status:
            die(f"No device '{name}'. Without tailscale there is only this machine, {devices[0].name}.")

        reason = tailnet.why_missing(status, name)
        if reason is not None:
            die(f"{name}: {reason}.")
        online = ", ".join(device.name for device in devices)
        die(f"No device '{name}' on the tailnet. Online: {online}.")

    if problem:
        warn(f"Cannot read the tailnet. {problem}")

    if device not in tailnet.select_devices(devices, config):
        die(f"Device '{device.name}' is left out by devices.only or devices.skip in {config_path()}.")

    return device


def _sessions_on(device: Device) -> list[Session]:
    """The sessions of one device, read the way the listing reads them, or the reason ssh gave up.

    A failed connection is not the same answer as no sessions here. The listing can afford to
    leave a silent machine out, and an attach that was asked for this machine by name cannot.
    """
    if device.is_self:
        return tmux.list_sessions()

    (probe,) = _probe_all([device])
    reason = probe_failure(probe)
    if reason is not None:
        die(f"{device.name}: {reason}.")
    return probe.sessions


def probe_failure(probe: Probe) -> str | None:
    """Why ssh gave no listing for the device, ``None`` when it connected and tmux answered.

    The reason reads after ``"<name>: "``, so the listing and ``attach`` can say it the same way.
    A host that answered, with no tmux or no tmux server on it, has nothing to explain: that is
    a machine without sessions, not a failure.
    """
    # A probe waiting on a Tailscale SSH check keeps no deadline, so only the others end up here
    if probe.returncode is not None and probe.returncode < 0:
        return f"ssh did not answer within {_SCAN_DEADLINE:g} seconds"

    if not probe.unreachable:
        return None

    reason = f"ssh could not connect to {probe.device.host}"
    if probe.login_url:
        reason += f" after Tailscale SSH asked for a check at {probe.login_url}"
    if probe.output:
        reason += f": {probe.output[-1]}"
    return reason


def _scan(devices: list[Device]) -> list[tuple[Device, list[Session]]]:
    """Every selected device and the sessions on it, with the devices that have none left out.

    This machine is read through the tmux binary, with no ssh in the way. The rest are probed
    over ssh, the canary alone first so that at most one authentication check is outstanding.
    A device ssh could not get a listing from is named on stderr with the reason, because a
    missing machine with no word about it reads as a machine with no sessions.
    """
    sessions: dict[Device, list[Session]] = {}

    for device in devices:
        if device.is_self:
            sessions[device] = tmux.list_sessions()

    for probe in _probe_all([device for device in devices if not device.is_self]):
        sessions[probe.device] = probe.sessions
        reason = probe_failure(probe)
        if reason is not None:
            warn(f"{probe.device.name}: {reason}. Its sessions are not listed.")

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
    _render(found, current)
    return select(picks, ask("Enter number or session@device to attach (Ctrl+C to cancel): "))


def _render(found: list[tuple[Device, list[Session]]], current: str) -> None:
    """The listing the picker and ``ls`` share: one block per device, numbered as ``flatten`` orders them."""
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
            row.append(label(session.name, device.name), style="green")
            if device.is_self and session.name == current:
                row.append(" ")
                row.append("[current]", style="yellow")
            line(row)
            line(Text("      ").append(session.path, style="dim"))

        line()


def select(picks: list[Pick], answer: str) -> Pick:
    """The pick an answer to the prompt means: a number from the list, or a name exactly as listed."""
    if _NUMBER.fullmatch(answer):
        number = int(answer) - 1
        if number < 0 or number >= len(picks):
            die(f"Selection out of range: {answer}")
        return picks[number]

    for pick in picks:
        if pick.label == answer:
            return pick

    die(f"Invalid selection: {answer}")


def _attach(device: Device, name: str, current: str) -> None:
    """Hand the terminal over to the session, replacing this process on the way out.

    The attach carries no ``-d``. A machine you are away from may well have a client of its own
    on the session, open for weeks, and reaching that session from the road is no reason to
    throw the other client off it.

    The target carries tmux's ``=`` prefix, which asks for the exact name. Without it tmux falls
    back to a prefix match, and ``notes`` would attach to ``notes-old`` the day ``notes`` is gone.
    """
    if device.is_self:
        _attach_locally(name, current)
        return

    # ssh joins everything after the host name into one string for a login shell on the other
    # side, where a session name with a space or a quote in it would come apart. Locally there
    # is no shell between the argv and tmux, which is why tmux-here needs none of this.
    # shlex.quote leaves "=notes" bare as safe, and zsh then expands a leading "=" into the path
    # of a command called notes, so the quotes are put on whether shlex thinks they are needed.
    target = "'" + _exact(name).replace("'", "'\"'\"'") + "'"
    try:
        exec_process(["ssh", "-t", device.host, "tmux", "attach", "-t", target])
    except OSError:
        die("ssh is not installed.")


def _exact(name: str) -> str:
    return f"={name}"


def _attach_locally(name: str, current: str) -> None:
    if name == current:
        warn(f"Already in session '{name}'.")
        return

    if tmux.inside_tmux():
        tmux.switch_client(_exact(name))
    else:
        tmux.exec_attach(_exact(name))


def main() -> None:
    bootstrap()
    app()
