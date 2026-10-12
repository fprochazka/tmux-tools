"""The devices on the Tailscale tailnet, and reading the tmux sessions on them over ssh."""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import Any

from . import tmux
from .config import Config
from .console import die, warn

# ssh's own patience is measured in minutes, which is too long to spend on a peer the
# coordination server calls online and the train does not.
CONNECT_TIMEOUT = 5

# Phones and tablets have no ssh server to reach.
_MOBILE = frozenset({"android", "ios"})

# The host part of the URL Tailscale SSH prints when it wants a browser login.
_LOGIN_URL = "login.tailscale.com"
_URL = re.compile(r"https://\S+")


@dataclass(frozen=True)
class Device:
    """One machine on the tailnet, under the name ``tailscale status`` gives it."""

    name: str
    host: str
    tagged: bool
    is_self: bool


def require_ssh() -> None:
    """ssh reaches the other machines, so it is needed only when one is about to be read.

    This machine answers through the tmux binary, and a machine with no ssh client still has
    its own sessions to list.
    """
    if shutil.which("ssh") is None:
        die("ssh is not installed.")


class TailnetUnavailable(Exception):
    """tailscale is installed and the tailnet cannot be read from it. The message says why."""


def status() -> dict[str, Any]:
    """The tailnet as ``tailscale status --json`` describes it, empty when there is none to read.

    Every way of failing here ends in the same place, an empty tailnet, which leaves the caller
    with this machine alone. That is the useful answer: a laptop whose tailscaled is stopped
    still has tmux sessions on it, and refusing to list them helps nobody.

    A machine without tailscale gets no warning. It is not on a tailnet, nothing about it is
    broken, and a line on every run would be noise. A tailscale that is installed and cannot
    answer does warn, because the user expected the other machines and is owed the reason they
    are missing.
    """
    try:
        return read()
    except TailnetUnavailable as problem:
        warn(f"Cannot read the tailnet, listing this machine only. {problem}")
        return {}


def read() -> dict[str, Any]:
    """The tailnet, empty without tailscale, and ``TailnetUnavailable`` when tailscale cannot answer.

    stderr is left alone, so tailscale explains a logged out or stopped daemon in its own words
    before the reason given here. A daemon that is stopped or logged out may still answer with
    JSON, describing a tailnet it is not on; ``BackendState`` is what tells that answer apart.
    """
    if shutil.which("tailscale") is None:
        return {}

    result = subprocess.run(["tailscale", "status", "--json"], stdout=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        raise TailnetUnavailable("Check that tailscaled runs and is logged in.")

    try:
        parsed = json.loads(result.stdout)
    except ValueError:
        raise TailnetUnavailable("tailscale status did not answer with JSON.") from None

    state = parsed.get("BackendState")
    if state is not None and state != "Running":
        raise TailnetUnavailable(f"tailscale is in state {state}, check that tailscaled runs and is logged in.")

    return parsed


def online_devices(tailnet: dict[str, Any]) -> list[Device]:
    """The devices worth scanning, this machine first and the peers by name.

    ``Online`` is the coordination server's opinion. It is the best signal available and still
    only an opinion, so the scan keeps a deadline of its own for the peers it is wrong about.

    This machine comes first so that the peers finish the list, right above the prompt. The
    number worth typing here is usually a session on another machine, and that puts it where
    the eye already is. ``tmux-here`` is the shorter way to a local session anyway.

    This machine stays in whatever its own record says, because it is the one device that needs
    no network to answer. Its sessions are read through the tmux binary rather than over ssh.

    A tailnet that yields nothing at all, because it could not be read or because it holds no
    device worth scanning, still leaves this machine, under the name the system gives it.
    """
    devices: list[Device] = []

    own = tailnet.get("Self")
    if own:
        devices.append(_device(own, is_self=True))

    peers: list[Device] = []
    for peer in (tailnet.get("Peer") or {}).values():
        if peer.get("Online") is not True:
            continue
        if is_mobile(peer):
            continue
        peers.append(_device(peer, is_self=False))

    devices.extend(sorted(peers, key=lambda device: device.name))
    return devices or [_local_device()]


def select_devices(devices: list[Device], config: Config) -> list[Device]:
    """Narrow the devices to what the config file asks for, keeping the order they came in.

    ``only`` is the whole answer when it is set, so ``skip`` is never read alongside it.
    """
    if config.only:
        wanted = set(config.only)
        return [device for device in devices if device.name in wanted]

    unwanted = set(config.skip)
    return [device for device in devices if device.name not in unwanted]


def unmatched_names(devices: list[Device], config: Config) -> list[str]:
    """Names in ``only`` that no device carries, usually a typo or a machine that has left."""
    present = {device.name for device in devices}
    return [name for name in config.only if name not in present]


def find_device(devices: list[Device], name: str) -> Device | None:
    """The device a typed name means: its host name first, its MagicDNS name second.

    The host name is what every listing shows, so it wins when one device's host name is another
    device's MagicDNS name. The MagicDNS name is what Tailscale keeps unique, which makes it the
    way to tell apart two machines that share a host name.
    """
    for device in devices:
        if device.name == name:
            return device
    for device in devices:
        if device.host == name:
            return device
    return None


def peer_named(tailnet: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The tailnet's own record of a device, online or not, under either of its names.

    For explaining why a name is missing from ``online_devices``, and for nothing else.
    """
    records = [tailnet.get("Self") or {}, *(tailnet.get("Peer") or {}).values()]
    for record in records:
        if record and name in (str(record.get("HostName") or ""), _ssh_host(record)):
            return record
    return None


def why_missing(tailnet: dict[str, Any], name: str) -> str | None:
    """Why a device the tailnet knows is not among ``online_devices`` under this name, ``None`` when unknown.

    The reason reads after ``"<name>: "``, so the listing and ``attach`` can say it the same way.
    """
    record = peer_named(tailnet, name)
    if record is None:
        return None

    host_name = str(record.get("HostName") or "")
    if host_name != name:
        return f"that is its MagicDNS name, the config file has to use its host name '{host_name}'"
    if record.get("Online") is not True:
        return "Tailscale reports it offline"
    if is_mobile(record):
        return "it is a phone or a tablet, which has no ssh server to reach"
    return "Tailscale does not list it as a device to scan"


def is_mobile(peer: dict[str, Any]) -> bool:
    return str(peer.get("OS") or "").lower() in _MOBILE


def choose_canary(devices: list[Device]) -> Device | None:
    """The device to probe on its own first, so one authentication check covers the tailnet.

    Tailscale SSH asks for a browser login once per tailnet, and the point of sending one host
    ahead is that eight of them do not produce eight URLs at once. A tagged node is the wrong
    volunteer: its ACLs may refuse ssh outright, and a refusal arrives without the check ever
    firing, which teaches the scan nothing.

    The devices arrive sorted, so the same tailnet picks the same canary every time.
    """
    remote = [device for device in devices if not device.is_self]
    if not remote:
        return None

    untagged = [device for device in remote if not device.tagged]
    return (untagged or remote)[0]


class Probe:
    """One host's session listing over ssh, read line by line while the connection runs.

    Reading as the output arrives is what makes the Tailscale SSH check bearable. When the
    tailnet wants a browser login, the remote side says so and the connection then waits;
    printing that line the moment it appears is the difference between a URL to click and a
    minute of silence. The same connection produces the listing afterwards, so a probe that
    has printed a URL is left alone to finish, however long the human takes.

    stderr is merged into stdout because it is not worth establishing which of the two carries
    the check. The listing survives the noise: a line counts as a session only when it has the
    shape of one, which nothing else the connection prints does.
    """

    def __init__(self, device: Device) -> None:
        self.device = device
        self.sessions: list[tmux.Session] = []
        self.waiting_for_login = False
        self.login_url = ""
        # Everything the connection printed that was not a session, ssh's complaints among it
        self.output: list[str] = []
        self.returncode: int | None = None
        self._process = subprocess.Popen(
            tmux.list_sessions_command(_ssh(device.host)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # Several probes and the picker would otherwise read the same terminal between
            # them. ssh still reaches /dev/tty for anything it has to ask a human.
            stdin=subprocess.DEVNULL,
            text=True,
        )

    def run(self) -> None:
        """Read the connection to its end. Runs in a worker thread; results land on the probe."""
        for raw in self._process.stdout:
            self._consume(raw.rstrip("\n"))
        self.returncode = self._process.wait()

    @property
    def unreachable(self) -> bool:
        """True when ssh itself failed or the connection was killed, as opposed to tmux answering.

        ssh exits 255 when it cannot connect or authenticate, which no tmux exit code collides
        with. A negative code is a connection killed at the scan deadline.
        """
        return self.returncode is not None and (self.returncode == 255 or self.returncode < 0)

    def stop(self) -> None:
        """Kill the connection. Harmless on one that has already finished."""
        self._process.kill()

    def _consume(self, text: str) -> None:
        if _LOGIN_URL in text:
            self.waiting_for_login = True
            found = _URL.search(text)
            self.login_url = found.group(0) if found else self.login_url
            warn(f"{self.device.name}: {text.lstrip('# ')}")
            return

        session = tmux.parse_session(text)
        if session is not None:
            self.sessions.append(session)
        elif text.strip():
            self.output.append(text)


def _ssh(host: str) -> tmux.Prefix:
    """The ssh command a probe runs behind.

    ``BatchMode=no`` leaves ssh able to ask, which Tailscale SSH now and then needs it to be.
    No ``-t``: a listing wants no terminal, and only the attach does.
    """
    return ("ssh", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}", "-o", "BatchMode=no", host)


def _local_device() -> Device:
    """This machine on its own, named by the system rather than by a tailnet.

    The two names need not agree. The system host name is the only one available here, and the
    config file is matched against whichever of them the listing shows.

    ``host`` goes unused, since a device marked ``is_self`` is read and attached through the
    tmux binary and never reached over ssh. It carries the host name anyway, because a field
    holding a sentinel costs more the first time someone reads it in a debugger.
    """
    name = socket.gethostname()
    return Device(name=name, host=name, tagged=False, is_self=True)


def _device(peer: dict[str, Any], *, is_self: bool) -> Device:
    return Device(
        name=str(peer.get("HostName") or ""),
        host=_ssh_host(peer),
        tagged=bool(peer.get("Tags")),
        is_self=is_self,
    )


def _ssh_host(peer: dict[str, Any]) -> str:
    """The name to hand ssh, taken from MagicDNS rather than from the host name.

    The two are not the same. A machine whose host name is ``acme-workshop`` may answer to
    ``workshop`` on the tailnet and to nothing else, so the host name is what a device is
    called and the MagicDNS name is what it is reached by. Tailscale SSH maps the identity, so
    there is no user to put in front of it.
    """
    dns = str(peer.get("DNSName") or "").rstrip(".")
    if not dns:
        return str(peer.get("HostName") or "")
    return dns.split(".", 1)[0]
