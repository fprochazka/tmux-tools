"""The two prompts of tmux-here, which have to agree on what a usable session name is."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from tmux_tools import tmux
from tmux_tools.commands import here
from tmux_tools.tmux import Pane

MATCHES = [Pane("alpha", "/repo/one"), Pane("beta", "/repo/two")]


@dataclass
class FakeTmux:
    """A tmux that records what it was asked to do instead of doing it."""

    calls: list[tuple[str, ...]] = field(default_factory=list)
    taken: set[str] = field(default_factory=set)


@pytest.fixture
def fake_tmux(monkeypatch: pytest.MonkeyPatch) -> FakeTmux:
    fake = FakeTmux()
    # Inside tmux, so that creating a session is two recorded calls and not an exec
    monkeypatch.setattr(tmux, "inside_tmux", lambda: True)
    monkeypatch.setattr(tmux, "session_exists", lambda name: name in fake.taken)
    monkeypatch.setattr(tmux, "switch_client", lambda name: fake.calls.append(("switch", name)))
    monkeypatch.setattr(tmux, "new_session_detached", lambda name, path: fake.calls.append(("new", name, path)))
    return fake


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch):
    def given(*typed: str) -> None:
        remaining = list(typed)

        def ask(question: str) -> str:
            # Running out means the prompt asked once more than the test expected
            assert remaining, f"unanswered prompt: {question}"
            return remaining.pop(0)

        monkeypatch.setattr(here, "ask", ask)

    return given


def test_a_number_attaches_to_that_session(fake_tmux: FakeTmux, answers):
    answers("2")
    here._pick_session(MATCHES, "/repo", "")
    assert fake_tmux.calls == [("switch", "beta")]


def test_a_name_starts_a_new_session_in_the_directory(fake_tmux: FakeTmux, answers):
    answers("gamma")
    here._pick_session(MATCHES, "/repo", "")
    assert fake_tmux.calls == [("new", "gamma", "/repo"), ("switch", "gamma")]


def test_a_number_out_of_range_is_not_a_name(fake_tmux: FakeTmux, answers):
    # Creating a session called "9" would be a worse answer to a typo than asking again
    answers("9", "1")
    here._pick_session(MATCHES, "/repo", "")
    assert fake_tmux.calls == [("switch", "alpha")]


def test_zero_is_not_a_name_either(fake_tmux: FakeTmux, answers):
    answers("0", "1")
    here._pick_session(MATCHES, "/repo", "")
    assert fake_tmux.calls == [("switch", "alpha")]


def test_an_empty_answer_asks_again(fake_tmux: FakeTmux, answers):
    answers("", "1")
    here._pick_session(MATCHES, "/repo", "")
    assert fake_tmux.calls == [("switch", "alpha")]


def test_a_name_another_session_holds_is_refused(fake_tmux: FakeTmux, answers):
    fake_tmux.taken.add("taken")
    answers("taken", "fresh")
    here._pick_session(MATCHES, "/repo", "")
    assert fake_tmux.calls == [("new", "fresh", "/repo"), ("switch", "fresh")]


def test_picking_the_session_you_are_in_does_nothing(fake_tmux: FakeTmux, answers):
    answers("1")
    here._pick_session(MATCHES, "/repo", "alpha")
    assert fake_tmux.calls == []


def test_a_name_that_only_looks_like_a_number_is_a_name(fake_tmux: FakeTmux, answers):
    answers("1a")
    here._pick_session(MATCHES, "/repo", "")
    assert fake_tmux.calls == [("new", "1a", "/repo"), ("switch", "1a")]


def test_the_other_prompt_refuses_a_taken_name_the_same_way(
    fake_tmux: FakeTmux, answers, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(here, "propose_names", lambda root: ["acme/shop"])
    fake_tmux.taken.add("acme/shop")
    answers("1", "acme/shop", "acme/shop-2")
    here._offer_new_session("/repo")
    assert fake_tmux.calls == [("new", "acme/shop-2", "/repo"), ("switch", "acme/shop-2")]
