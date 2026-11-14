"""Reading a probe over ssh, the picker and its numbering, tmux-there ls, tmux-there attach, and the completion."""

from __future__ import annotations

import json
import shlex
import shutil
import socket
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import CliRunner

from tmux_tools import cache, tailnet, tmux
from tmux_tools.commands import there
from tmux_tools.commands.there import Pick, flatten
from tmux_tools.config import Config
from tmux_tools.console import silenced
from tmux_tools.tailnet import Device, Probe
from tmux_tools.tmux import Session, parse_session

# What one connection printed: the Tailscale SSH check, the user going to a browser, and then
# the listing the same connection produced once they had.
TRANSCRIPT = [
    "# Tailscale SSH requires an additional check.",
    "# To authenticate, visit: https://login.tailscale.com/a/1f2e3d4c5b6a",
    "# Authentication checked with Tailscale SSH.",
    "work/releases\t/home/you/devel/releases",
    "personal/notes\t/home/you/notes",
]

HQ = Device(name="hq", host="hq", tagged=False, is_self=True)
LAPTOP = Device(name="acme-laptop", host="laptop", tagged=False, is_self=False)


@pytest.fixture(autouse=True)
def cache_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    # Every full scan writes the names it found, and the test run must not write them over yours
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache"


def parse_all(lines: list[str]) -> list[Session]:
    return [session for session in map(parse_session, lines) if session is not None]


def test_the_listing_survives_the_authentication_noise():
    assert parse_all(TRANSCRIPT) == [
        Session("work/releases", "/home/you/devel/releases"),
        Session("personal/notes", "/home/you/notes"),
    ]


def test_a_connection_that_only_complains_yields_nothing():
    complaints = [
        "error connecting to /tmp/tmux-1000/default (No such file or directory)",
        "zsh:1: command not found: tmux",
        "ssh: connect to host cache port 22: Connection refused",
        "",
    ]
    assert parse_all(complaints) == []


def test_the_numbering_runs_across_devices():
    found = [
        (HQ, [Session("personal/notes", "/home/you/notes")]),
        (LAPTOP, [Session("work/releases", "/srv/releases"), Session("work/db", "/srv/db")]),
    ]
    assert [pick.session.name for pick in flatten(found)] == ["personal/notes", "work/releases", "work/db"]


def test_a_number_maps_back_to_its_device_and_its_session():
    found = [
        (HQ, [Session("personal/notes", "/home/you/notes")]),
        (LAPTOP, [Session("work/releases", "/srv/releases"), Session("work/db", "/srv/db")]),
    ]
    assert flatten(found)[2] == Pick(LAPTOP, Session("work/db", "/srv/db"))


def test_nothing_anywhere_is_an_empty_list():
    assert flatten([]) == []


def test_this_machine_is_read_through_tmux_and_never_handed_to_a_probe(monkeypatch: pytest.MonkeyPatch):
    local = Session("personal/notes", "/home/you/notes")
    probed: list[Device] = []

    def record(devices: list[Device], deadline: float | None = None) -> list[Probe]:
        probed.extend(devices)
        return []

    monkeypatch.setattr(tmux, "list_sessions", lambda: [local])
    monkeypatch.setattr(there, "_probe_all", record)

    assert there._scan([HQ, LAPTOP]) == [(HQ, [local])]
    assert probed == [LAPTOP]


def test_this_machine_on_its_own_needs_no_ssh(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert there._probe_all([]) == []


def test_a_label_is_the_session_then_the_device():
    assert Pick(LAPTOP, Session("acme/shop", "/srv/shop")).label == "acme/shop@acme-laptop"


def test_a_target_splits_on_the_last_at_sign():
    assert there.parse_target("acme/shop/new-checkout@attic") == ("acme/shop/new-checkout", "attic")
    assert there.parse_target("you@work@attic") == ("you@work", "attic")


@pytest.mark.parametrize("target", ["attic", "@attic", "notes@", ""])
def test_a_target_without_both_halves_is_refused(target: str):
    with pytest.raises(SystemExit):
        there.parse_target(target)


PICKS = [
    Pick(HQ, Session("personal/notes", "/home/you/notes")),
    Pick(LAPTOP, Session("work/releases", "/srv/releases")),
]


def test_the_prompt_takes_a_number():
    assert there.select(PICKS, "2") == PICKS[1]


def test_the_prompt_takes_a_name_exactly_as_listed():
    assert there.select(PICKS, "work/releases@acme-laptop") == PICKS[1]


@pytest.mark.parametrize("answer", ["0", "3", "work/releases", "work/releases@laptop", "nope"])
def test_the_prompt_refuses_anything_not_on_the_list(answer: str):
    with pytest.raises(SystemExit):
        there.select(PICKS, answer)


@dataclass
class FakeProbe:
    """What a probe of one host left behind, without an ssh connection behind it."""

    device: Device = LAPTOP
    sessions: list[Session] = field(default_factory=list)
    returncode: int = 0
    output: list[str] = field(default_factory=list)
    login_url: str = ""

    @property
    def unreachable(self) -> bool:
        return self.returncode == 255 or self.returncode < 0


@dataclass
class World:
    """Everything tmux-there reads and does, held in the test instead of the machine."""

    tailnet: dict
    config: Config = field(default_factory=Config)
    local: list[Session] = field(default_factory=list)
    probe: FakeProbe = field(default_factory=FakeProbe)
    unavailable: str = ""
    inside_tmux: bool = False
    probed: list[Device] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    status = json.loads((Path(__file__).parent / "tailscale-status.json").read_text())
    world = World(tailnet=status)

    def read() -> dict:
        if world.unavailable:
            raise tailnet.TailnetUnavailable(world.unavailable)
        return world.tailnet

    def probe_all(devices: list[Device], deadline: float | None = None) -> list[FakeProbe]:
        world.probed.extend(devices)
        return [replace(world.probe, device=device) for device in devices]

    def exec_process(argv: list[str]) -> NoReturn:
        world.calls.append(("exec", *argv))
        raise SystemExit(0)

    def exec_attach(name: str) -> NoReturn:
        world.calls.append(("attach", name))
        raise SystemExit(0)

    monkeypatch.setattr(tmux, "require_installed", lambda: None)
    monkeypatch.setattr(tmux, "current_session", lambda: "")
    monkeypatch.setattr(tmux, "inside_tmux", lambda: world.inside_tmux)
    monkeypatch.setattr(tmux, "list_sessions", lambda: world.local)
    monkeypatch.setattr(tmux, "exec_attach", exec_attach)
    monkeypatch.setattr(tmux, "switch_client", lambda name: world.calls.append(("switch", name)))
    monkeypatch.setattr(tailnet, "read", read)
    monkeypatch.setattr(there, "load_config", lambda: world.config)
    monkeypatch.setattr(there, "_probe_all", probe_all)
    monkeypatch.setattr(there, "exec_process", exec_process)
    return world


def attach(target: str) -> None:
    with pytest.raises(SystemExit) as exit:
        there.attach(target)
    if exit.value.code:
        raise AssertionError(f"attach exited with {exit.value.code}")


def refused(target: str, capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as exit:
        there.attach(target)
    assert exit.value.code == 1
    return capsys.readouterr().err


def test_a_remote_session_is_attached_over_ssh_by_its_exact_name(world: World):
    world.probe = FakeProbe(sessions=[Session("work/db", "/srv/db")])
    attach("work/db@acme-laptop")
    assert world.calls == [("exec", "ssh", "-t", "laptop", "tmux", "attach", "-t", "'=work/db'")]


def test_only_the_named_device_is_read(world: World):
    world.probe = FakeProbe(sessions=[Session("work/db", "/srv/db")])
    attach("work/db@acme-laptop")
    assert [device.name for device in world.probed] == ["acme-laptop"]


def test_a_device_is_also_found_by_its_magicdns_name(world: World):
    world.probe = FakeProbe(sessions=[Session("work/db", "/srv/db")])
    attach("work/db@laptop")
    assert world.calls == [("exec", "ssh", "-t", "laptop", "tmux", "attach", "-t", "'=work/db'")]


def test_a_session_name_is_quoted_for_the_remote_shell(world: World):
    world.probe = FakeProbe(sessions=[Session("it's notes", "/srv/notes")])
    attach("it's notes@acme-laptop")
    target = world.calls[0][-1]
    assert target == "'=it'\"'\"'s notes'"
    assert shlex.split(target) == ["=it's notes"]


def test_a_session_on_this_machine_is_attached_through_tmux(world: World):
    world.local = [Session("personal/notes", "/home/you/notes")]
    attach("personal/notes@hq")
    assert world.calls == [("attach", "=personal/notes")]
    assert world.probed == []


def test_a_session_on_this_machine_is_switched_to_from_inside_tmux(world: World):
    world.local = [Session("personal/notes", "/home/you/notes")]
    world.inside_tmux = True
    there.attach("personal/notes@hq")
    assert world.calls == [("switch", "=personal/notes")]


def test_a_missing_session_is_reported_and_never_created(world: World, capsys: pytest.CaptureFixture[str]):
    world.probe = FakeProbe(sessions=[Session("work/db", "/srv/db")])
    assert "No session 'work' on acme-laptop" in refused("work@acme-laptop", capsys)
    assert world.calls == []


def test_a_device_with_no_sessions_says_so(world: World, capsys: pytest.CaptureFixture[str]):
    world.probe = FakeProbe(returncode=1, output=["no server running on /tmp/tmux-1000/default"])
    assert "No tmux session runs on acme-laptop" in refused("work@acme-laptop", capsys)


def test_a_failed_ssh_connection_is_not_a_missing_session(world: World, capsys: pytest.CaptureFixture[str]):
    world.probe = FakeProbe(returncode=255, output=["ssh: connect to host laptop port 22: Connection refused"])
    message = refused("work@acme-laptop", capsys)
    assert "acme-laptop: ssh could not connect to laptop: ssh: connect to host laptop port 22" in message


def test_a_connection_killed_at_the_deadline_says_so(world: World, capsys: pytest.CaptureFixture[str]):
    world.probe = FakeProbe(returncode=-9)
    assert "acme-laptop: ssh did not answer within 15 seconds." in refused("work@acme-laptop", capsys)


def test_an_offline_device_is_reported_as_offline(world: World, capsys: pytest.CaptureFixture[str]):
    assert "attic: Tailscale reports it offline." in refused("notes@attic", capsys)
    assert world.probed == []


def test_a_phone_is_reported_as_unreachable(world: World, capsys: pytest.CaptureFixture[str]):
    assert "Pixel 9a: it is a phone or a tablet" in refused("notes@Pixel 9a", capsys)


def test_an_unknown_device_lists_the_online_ones(world: World, capsys: pytest.CaptureFixture[str]):
    assert "No device 'typo' on the tailnet. Online: hq, acme-laptop" in refused("notes@typo", capsys)


def test_a_device_the_config_leaves_out_cannot_be_attached(world: World, capsys: pytest.CaptureFixture[str]):
    world.config = Config(skip=("acme-laptop",))
    assert "Device 'acme-laptop' is left out by devices.only or devices.skip" in refused("work@laptop", capsys)
    assert world.probed == []


def test_a_tailnet_that_cannot_be_read_is_reported(world: World, capsys: pytest.CaptureFixture[str]):
    world.unavailable = "Check that tailscaled runs and is logged in."
    message = refused("work/db@acme-laptop", capsys)
    assert "Cannot read the tailnet to find 'acme-laptop'. Check that tailscaled runs" in message


def test_a_tailnet_that_cannot_be_read_still_leaves_this_machine(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(socket, "gethostname", lambda: "workbench")
    world.unavailable = "Check that tailscaled runs and is logged in."
    world.local = [Session("personal/notes", "/home/you/notes")]
    attach("personal/notes@workbench")
    assert world.calls == [("attach", "=personal/notes")]
    assert "Cannot read the tailnet" in capsys.readouterr().err


TWO_DEVICES = [
    (HQ, [Session("personal/notes", "/home/you/notes")]),
    (LAPTOP, [Session("work/releases", "/srv/releases"), Session("work/db", "/srv/db")]),
]

LISTING = """
hq (local)
   1) personal/notes@hq [current]
      /home/you/notes

acme-laptop
   2) work/releases@acme-laptop
      /srv/releases
   3) work/db@acme-laptop
      /srv/db

"""


def test_ls_prints_the_numbered_listing_grouped_by_device(world: World, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: TWO_DEVICES)
    monkeypatch.setattr(tmux, "current_session", lambda: "personal/notes")
    result = CliRunner().invoke(there.app, ["ls"])
    assert result.exit_code == 0
    assert result.stdout == LISTING


def test_the_picker_is_the_ls_listing_and_a_prompt(world: World, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: TWO_DEVICES)
    monkeypatch.setattr(tmux, "current_session", lambda: "personal/notes")
    monkeypatch.setattr(there, "ask", lambda question: "3")
    result = CliRunner().invoke(there.app, [])
    assert result.exit_code == 0
    assert result.stdout == LISTING
    assert world.calls == [("exec", "ssh", "-t", "laptop", "tmux", "attach", "-t", "'=work/db'")]


def test_ls_with_nothing_anywhere_prints_nothing(world: World, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: [])
    result = CliRunner().invoke(there.app, ["ls"])
    assert result.exit_code == 0
    assert result.stdout == ""


def test_no_command_is_the_picker(world: World, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: [(LAPTOP, [Session("work/db", "/srv/db")])])
    monkeypatch.setattr(there, "ask", lambda question: "work/db@acme-laptop")
    result = CliRunner().invoke(there.app, [])
    assert result.exit_code == 0
    assert "work/db@acme-laptop" in result.stdout
    assert world.calls == [("exec", "ssh", "-t", "laptop", "tmux", "attach", "-t", "'=work/db'")]


def scan_warnings(probe: FakeProbe, world: World, capsys: pytest.CaptureFixture[str]) -> list[str]:
    world.probe = probe
    there._scan([LAPTOP])
    return capsys.readouterr().err.splitlines()


def test_the_scan_names_a_device_ssh_could_not_connect_to(world: World, capsys: pytest.CaptureFixture[str]):
    probe = FakeProbe(returncode=255, output=["ssh: connect to host laptop port 22: Connection refused"])
    assert scan_warnings(probe, world, capsys) == [
        "acme-laptop: ssh could not connect to laptop: ssh: connect to host laptop port 22: Connection refused."
        " Its sessions are not listed."
    ]


def test_the_scan_names_a_device_that_failed_after_a_tailscale_ssh_check(
    world: World, capsys: pytest.CaptureFixture[str]
):
    url = "https://login.tailscale.com/a/1f2e3d4c5b6a"
    probe = FakeProbe(returncode=255, login_url=url, output=["Connection closed by laptop"])
    assert scan_warnings(probe, world, capsys) == [
        f"acme-laptop: ssh could not connect to laptop after Tailscale SSH asked for a check at {url}:"
        " Connection closed by laptop. Its sessions are not listed."
    ]


def test_the_scan_names_a_device_that_did_not_answer_in_time(world: World, capsys: pytest.CaptureFixture[str]):
    assert scan_warnings(FakeProbe(returncode=-9), world, capsys) == [
        "acme-laptop: ssh did not answer within 15 seconds. Its sessions are not listed."
    ]


def test_the_scan_says_nothing_about_a_device_that_answered_without_sessions(
    world: World, capsys: pytest.CaptureFixture[str]
):
    probe = FakeProbe(returncode=1, output=["no server running on /tmp/tmux-1000/default"])
    assert scan_warnings(probe, world, capsys) == []


def test_the_config_names_get_the_reason_they_are_not_listed(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: [])
    world.config = Config(only=("attic", "Pixel 9a", "laptop", "typo", "workshop"))
    there._collect()
    assert capsys.readouterr().err.splitlines() == [
        "attic: Tailscale reports it offline. Its sessions are not listed.",
        "Pixel 9a: it is a phone or a tablet, which has no ssh server to reach. Its sessions are not listed.",
        "laptop: that is its MagicDNS name, the config file has to use its host name 'acme-laptop'."
        " Its sessions are not listed.",
        "The config file asks for 'typo', and no device found matches it.",
    ]


def kept() -> str:
    return cache.sessions_path().read_text()


def test_ls_keeps_the_names_it_listed_for_the_completion(world: World, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: TWO_DEVICES)
    CliRunner().invoke(there.app, ["ls"])
    assert kept() == "personal/notes@hq\nwork/releases@acme-laptop\nwork/db@acme-laptop\n"


def test_nothing_anywhere_is_kept_as_an_empty_list(world: World, monkeypatch: pytest.MonkeyPatch):
    cache.save_sessions(["work/db@acme-laptop"])
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: [])
    CliRunner().invoke(there.app, ["ls"])
    assert kept() == ""


def test_attach_reads_one_device_and_keeps_no_names(world: World):
    world.probe = FakeProbe(sessions=[Session("work/db", "/srv/db")])
    attach("work/db@acme-laptop")
    assert not cache.sessions_path().exists()


def test_the_kept_names_are_replaced_and_never_rewritten_in_place(cache_home: Path):
    cache.save_sessions(["old@hq"])
    with cache.sessions_path().open() as reader:
        cache.save_sessions(["new@hq", "my notes@hq"])
        # A reader that opened the file before the scan finished still sees the whole old list
        assert reader.read() == "old@hq\n"
    assert kept() == "new@hq\nmy notes@hq\n"
    assert [path.name for path in cache.sessions_path().parent.iterdir()] == ["there-sessions"]


def test_a_cache_that_cannot_be_written_is_no_reason_to_fail(cache_home: Path):
    cache_home.write_text("a file where the directory should be")
    cache.save_sessions(["work/db@acme-laptop"])


def test_ls_names_prints_bare_names_and_nothing_on_stderr(world: World, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: TWO_DEVICES)
    monkeypatch.setattr(tmux, "current_session", lambda: "personal/notes")
    # A name in only that matches no device warns in every other scan
    world.config = Config(only=("hq", "acme-laptop", "typo"))
    result = CliRunner().invoke(there.app, ["ls", "--names"])
    assert result.exit_code == 0
    assert result.stdout == "personal/notes@hq\nwork/releases@acme-laptop\nwork/db@acme-laptop\n"
    assert result.stderr == ""
    assert kept() == result.stdout


def test_ls_names_keeps_the_completion_deadline_for_tailscale_and_the_probes(
    world: World, monkeypatch: pytest.MonkeyPatch
):
    seen: dict[str, float | None] = {}

    def status(timeout: float | None = None) -> dict:
        seen["timeout"] = timeout
        return world.tailnet

    def scan(devices: list[Device], deadline: float | None = None) -> list:
        seen["deadline"] = deadline
        return []

    monkeypatch.setattr(tailnet, "status", status)
    monkeypatch.setattr(there, "_scan", scan)
    started = time.monotonic()
    CliRunner().invoke(there.app, ["ls", "--names"])
    ended = time.monotonic()
    assert 0 < seen["timeout"] <= 5
    assert started + 5 <= seen["deadline"] <= ended + 5


def test_ls_without_names_waits_as_long_as_it_takes(world: World, monkeypatch: pytest.MonkeyPatch):
    seen: list[float | None] = []
    monkeypatch.setattr(tailnet, "status", lambda timeout=None: seen.append(timeout) or world.tailnet)
    monkeypatch.setattr(there, "_scan", lambda devices, deadline=None: seen.append(deadline) or [])
    CliRunner().invoke(there.app, ["ls"])
    assert seen == [None, None]


def scripted_probes(monkeypatch: pytest.MonkeyPatch, scripts: dict[str, str]) -> list[str]:
    """Make each device's ssh connection a local shell script, and record the batch mode asked for."""
    batch_modes: list[str] = []

    def ssh(host: str, *, batch: bool = False) -> tuple[str, ...]:
        batch_modes.append(f"{host}:{batch}")
        return ("sh", "-c", scripts[host], "sh")

    monkeypatch.setattr(tailnet, "_ssh", ssh)
    monkeypatch.setattr(tailnet, "require_ssh", lambda: None)
    return batch_modes


LOGIN = "echo '# To authenticate, visit: https://login.tailscale.com/a/1f2e3d4c5b6a'; exec sleep 30"
ANSWER = "printf 'work/db\\t/srv/db\\n'"


def test_a_hurried_scan_leaves_out_a_device_that_asks_for_a_login(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    cache_box = Device(name="cache", host="cache", tagged=False, is_self=False)
    batch_modes = scripted_probes(monkeypatch, {"laptop": LOGIN, "cache": ANSWER})
    started = time.monotonic()
    with silenced():
        probes = there._probe_all([LAPTOP, cache_box], started + 5)
    assert time.monotonic() - started < 2
    assert {probe.device.name: [s.name for s in probe.sessions] for probe in probes} == {
        "acme-laptop": [],
        "cache": ["work/db"],
    }
    assert sorted(batch_modes) == ["cache:True", "laptop:True"]
    assert capsys.readouterr().err == ""


def test_a_hurried_scan_stops_at_its_deadline(monkeypatch: pytest.MonkeyPatch):
    scripted_probes(monkeypatch, {"laptop": "exec sleep 30"})
    started = time.monotonic()
    (probe,) = there._probe_all([LAPTOP], started + 0.5)
    assert 0.5 <= time.monotonic() - started < 2
    assert probe.unreachable


def test_a_watched_scan_asks_ssh_to_be_able_to_ask(monkeypatch: pytest.MonkeyPatch):
    batch_modes = scripted_probes(monkeypatch, {"laptop": ANSWER})
    (probe,) = there._probe_all([LAPTOP])
    assert batch_modes == ["laptop:False"]
    assert [session.name for session in probe.sessions] == ["work/db"]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_the_completion_scripts_read_the_file_ls_writes(shell: str, cache_home: Path):
    result = CliRunner().invoke(there.app, ["completion", shell])
    assert result.exit_code == 0
    relative = cache.sessions_path().relative_to(cache_home)
    assert "${XDG_CACHE_HOME:-$HOME/.cache}/" + str(relative) in result.stdout
    assert "tmux-there ls --names" in result.stdout
