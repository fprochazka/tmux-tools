"""Reading a probe over ssh, and the numbering the picker puts on what comes back."""

from __future__ import annotations

import shutil

import pytest

from tmux_tools import tmux
from tmux_tools.commands import there
from tmux_tools.commands.there import Pick, flatten
from tmux_tools.tailnet import Device, Probe
from tmux_tools.tmux import Session, parse_session

# What one connection printed: the Tailscale SSH check, the user going to a browser, and then
# the listing the same connection produced once they had.
TRANSCRIPT = [
    "# Tailscale SSH requires an additional check.",
    "# To authenticate, visit: https://login.tailscale.com/a/l80584f9391289",
    "# Authentication checked with Tailscale SSH.",
    "work/releases\t/home/fp/devel/releases",
    "personal/notes\t/home/fp/notes",
]

HQ = Device(name="hq", host="hq", tagged=False, is_self=True)
LAPTOP = Device(name="acme-laptop", host="laptop", tagged=False, is_self=False)


def parse_all(lines: list[str]) -> list[Session]:
    return [session for session in map(parse_session, lines) if session is not None]


def test_the_listing_survives_the_authentication_noise():
    assert parse_all(TRANSCRIPT) == [
        Session("work/releases", "/home/fp/devel/releases"),
        Session("personal/notes", "/home/fp/notes"),
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
        (HQ, [Session("personal/notes", "/home/fp/notes")]),
        (LAPTOP, [Session("work/releases", "/srv/releases"), Session("work/db", "/srv/db")]),
    ]
    assert [pick.session.name for pick in flatten(found)] == ["personal/notes", "work/releases", "work/db"]


def test_a_number_maps_back_to_its_device_and_its_session():
    found = [
        (HQ, [Session("personal/notes", "/home/fp/notes")]),
        (LAPTOP, [Session("work/releases", "/srv/releases"), Session("work/db", "/srv/db")]),
    ]
    assert flatten(found)[2] == Pick(LAPTOP, Session("work/db", "/srv/db"))


def test_nothing_anywhere_is_an_empty_list():
    assert flatten([]) == []


def test_this_machine_is_read_through_tmux_and_never_handed_to_a_probe(monkeypatch: pytest.MonkeyPatch):
    local = Session("personal/notes", "/home/fp/notes")
    probed: list[Device] = []

    def record(devices: list[Device]) -> list[Probe]:
        probed.extend(devices)
        return []

    monkeypatch.setattr(tmux, "list_sessions", lambda: [local])
    monkeypatch.setattr(there, "_probe_all", record)

    assert there._scan([HQ, LAPTOP]) == [(HQ, [local])]
    assert probed == [LAPTOP]


def test_this_machine_on_its_own_needs_no_ssh(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert there._probe_all([]) == []
