"""Pane matching: the half of tmux-here that decides which sessions belong to a directory."""

from __future__ import annotations

from tmux_tools.tmux import Pane, is_within, sessions_in


def test_root_itself_counts_as_within():
    assert is_within("/foo/bar", "/foo/bar")


def test_below_the_root_counts_as_within():
    assert is_within("/foo/bar/baz", "/foo/bar")


def test_matching_is_segment_aware():
    # A prefix comparison without the separator would call /foo/bar a child of /foo/ba
    assert not is_within("/foo/bar", "/foo/ba")


def test_a_sibling_is_not_within():
    assert not is_within("/foo/barn", "/foo/bar")


def test_the_filesystem_root_matches_only_itself():
    # Inherited from the bash tmux-here, whose "$root"/* glob becomes //* for this root and
    # matches nothing. Run from /, tmux-here therefore finds only panes sitting in / itself.
    assert is_within("/", "/")
    assert not is_within("/foo", "/")


def test_one_entry_per_session_keeping_the_first_matching_pane():
    panes = [
        Pane("alpha", "/repo/one"),
        Pane("alpha", "/repo/two"),
        Pane("beta", "/repo/three"),
    ]
    assert sessions_in(panes, ("/repo",)) == [Pane("alpha", "/repo/one"), Pane("beta", "/repo/three")]


def test_a_session_is_represented_by_its_first_matching_pane_not_its_first_pane():
    panes = [
        Pane("alpha", "/elsewhere"),
        Pane("alpha", "/repo/two"),
    ]
    assert sessions_in(panes, ("/repo",)) == [Pane("alpha", "/repo/two")]


def test_tmux_order_is_preserved():
    panes = [Pane("zulu", "/repo/z"), Pane("alpha", "/repo/a")]
    assert [pane.session for pane in sessions_in(panes, ("/repo",))] == ["zulu", "alpha"]


def test_a_pane_under_either_root_matches():
    panes = [Pane("alpha", "/logical/link/sub")]
    assert sessions_in(panes, ("/physical/real", "/logical/link")) == panes


def test_panes_without_a_session_name_are_skipped():
    assert sessions_in([Pane("", "/repo/one")], ("/repo",)) == []
