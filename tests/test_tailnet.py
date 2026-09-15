"""Device selection: which machines tmux-there scans, and which one it sends ahead."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from tmux_tools.config import Config
from tmux_tools.tailnet import Device, choose_canary, online_devices, select_devices, status, unmatched_names


@pytest.fixture
def tailnet() -> dict:
    # Trimmed from a real "tailscale status --json": peers keyed by node key, a DNS name that
    # is not the host name, tags on the service nodes, and Online as the peer's own field.
    return json.loads((Path(__file__).parent / "tailscale-status.json").read_text())


@pytest.fixture
def hostname(monkeypatch: pytest.MonkeyPatch) -> str:
    # The machine standing in for a tailnet is named by the system, so the name has to be
    # pinned here rather than taken from whichever machine runs the tests.
    monkeypatch.setattr(socket, "gethostname", lambda: "workbench")
    return "workbench"


def tailscale(monkeypatch: pytest.MonkeyPatch, *, installed: bool = True, returncode: int = 0, stdout: str = "{}"):
    """What the tailscale binary is, and what it answers, for one test. Nothing is run."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tailscale" if installed else None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, returncode, stdout))


def names(devices: list[Device]) -> list[str]:
    return [device.name for device in devices]


def device(name: str, *, tagged: bool = False, is_self: bool = False) -> Device:
    return Device(name=name, host=name, tagged=tagged, is_self=is_self)


def test_this_machine_comes_first_and_the_peers_by_name(tailnet: dict):
    assert names(online_devices(tailnet)) == ["hq", "acme-laptop", "cache", "runner", "workshop"]


def test_only_this_machine_is_marked_as_local(tailnet: dict):
    assert [d.is_self for d in online_devices(tailnet)] == [True, False, False, False, False]


def test_an_offline_peer_is_not_scanned(tailnet: dict):
    assert "attic" not in names(online_devices(tailnet))


def test_phones_and_tablets_are_not_scanned(tailnet: dict):
    scanned = names(online_devices(tailnet))
    assert "Pixel 9a" not in scanned
    assert "slate" not in scanned


def test_the_ssh_host_is_the_magicdns_name_and_not_the_host_name(tailnet: dict):
    laptop = next(d for d in online_devices(tailnet) if d.name == "acme-laptop")
    assert laptop.host == "laptop"


def test_tags_are_carried_over(tailnet: dict):
    tagged = [d.name for d in online_devices(tailnet) if d.tagged]
    assert tagged == ["cache", "runner"]


def test_without_a_config_file_everything_online_is_scanned(tailnet: dict):
    devices = online_devices(tailnet)
    assert select_devices(devices, Config()) == devices


def test_only_selects_those_devices_and_keeps_the_order(tailnet: dict):
    devices = online_devices(tailnet)
    assert names(select_devices(devices, Config(only=("workshop", "hq")))) == ["hq", "workshop"]


def test_skip_leaves_the_rest(tailnet: dict):
    devices = online_devices(tailnet)
    assert names(select_devices(devices, Config(skip=("cache", "runner")))) == ["hq", "acme-laptop", "workshop"]


def test_only_wins_and_skip_is_not_read(tailnet: dict):
    devices = online_devices(tailnet)
    config = Config(only=("cache", "runner"), skip=("cache",))
    assert names(select_devices(devices, config)) == ["cache", "runner"]


def test_a_name_in_only_that_matches_no_device_is_reported(tailnet: dict):
    devices = online_devices(tailnet)
    assert unmatched_names(devices, Config(only=("workshop", "attic", "typo"))) == ["attic", "typo"]


def test_a_name_in_skip_needs_no_report(tailnet: dict):
    devices = online_devices(tailnet)
    assert unmatched_names(devices, Config(skip=("gone",))) == []


def test_the_canary_is_the_first_untagged_device(tailnet: dict):
    canary = choose_canary(online_devices(tailnet))
    assert canary is not None
    assert canary.name == "acme-laptop"


def test_the_canary_is_the_same_on_every_run(tailnet: dict):
    devices = online_devices(tailnet)
    assert choose_canary(devices) == choose_canary(devices)


def test_a_tagged_device_is_the_canary_only_when_there_is_no_other():
    devices = [device("hq", is_self=True), device("runner", tagged=True), device("cache", tagged=True)]
    canary = choose_canary(devices)
    assert canary is not None
    assert canary.name == "runner"


def test_this_machine_is_never_the_canary():
    assert choose_canary([device("hq", is_self=True)]) is None


def test_a_tailnet_that_cannot_be_read_still_lists_this_machine(hostname: str):
    assert names(online_devices({})) == [hostname]


def test_a_tailnet_with_nothing_worth_scanning_still_lists_this_machine(hostname: str):
    offline_only = {"Peer": {"nodekey:1": {"HostName": "attic", "Online": False}}}
    assert names(online_devices(offline_only)) == [hostname]


def test_this_machine_is_the_local_device_of_that_list():
    fallback = online_devices({})[0]
    assert fallback.is_self is True
    assert fallback.tagged is False


def test_this_machine_is_never_probed_even_when_it_is_the_only_device():
    assert choose_canary(online_devices({})) is None


def test_the_config_narrows_this_machine_like_any_other_device(hostname: str):
    fallback = online_devices({})
    assert select_devices(fallback, Config(skip=(hostname,))) == []
    assert names(select_devices(fallback, Config(only=(hostname,)))) == [hostname]
    assert names(select_devices(fallback, Config(only=("hq",)))) == []


def test_a_name_in_only_is_reported_against_this_machine_too(hostname: str):
    assert unmatched_names(online_devices({}), Config(only=(hostname, "hq"))) == ["hq"]


def test_a_machine_without_tailscale_reads_an_empty_tailnet_and_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    tailscale(monkeypatch, installed=False)
    assert status() == {}
    assert capsys.readouterr().out == ""


def test_a_tailscale_that_cannot_answer_reads_an_empty_tailnet_and_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    tailscale(monkeypatch, returncode=1, stdout="")
    assert status() == {}
    assert "Cannot read the tailnet" in capsys.readouterr().out


def test_an_answer_that_is_not_json_reads_an_empty_tailnet_and_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    tailscale(monkeypatch, stdout="Tailscale is stopped.\n")
    assert status() == {}
    assert "Cannot read the tailnet" in capsys.readouterr().out


def test_a_tailnet_that_answers_is_read_as_it_is(monkeypatch: pytest.MonkeyPatch):
    tailscale(monkeypatch, stdout='{"Self": {"HostName": "hq", "DNSName": "hq.example.ts.net."}}')
    assert names(online_devices(status())) == ["hq"]
