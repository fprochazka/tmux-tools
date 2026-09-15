"""Name proposals: the other half of tmux-here that a port can silently get wrong."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tmux_tools.naming import propose_names


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = (tmp_path / "acme-shop").resolve()
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "commit", "-q", "--allow-empty", "-m", "init")
    git(root, "remote", "add", "origin", "git@github.com:acme/shop.git")
    return root


def test_outside_a_git_repository_there_are_no_proposals(tmp_path: Path):
    assert propose_names(str(tmp_path.resolve())) == []


def test_the_default_branch_is_left_out_of_the_name(repo: Path):
    assert propose_names(str(repo)) == ["acme/shop"]


def test_another_branch_is_offered_with_its_owner_segment_stripped_first(repo: Path):
    git(repo, "checkout", "-q", "-b", "alice/some-work")
    assert propose_names(str(repo)) == ["acme/shop/some-work", "acme/shop/alice/some-work", "acme/shop"]


def test_only_the_first_branch_segment_is_an_owner(repo: Path):
    git(repo, "checkout", "-q", "-b", "alice/feature/thing")
    assert propose_names(str(repo)) == ["acme/shop/feature/thing", "acme/shop/alice/feature/thing", "acme/shop"]


def test_a_branch_without_an_owner_segment_is_proposed_once(repo: Path):
    git(repo, "checkout", "-q", "-b", "some-work")
    assert propose_names(str(repo)) == ["acme/shop/some-work", "acme/shop"]


def test_a_detached_head_carries_no_branch_name(repo: Path):
    git(repo, "checkout", "-q", "--detach", "HEAD")
    assert propose_names(str(repo)) == ["acme/shop"]


def test_master_is_a_default_branch_too(repo: Path):
    git(repo, "checkout", "-q", "-b", "master")
    assert propose_names(str(repo)) == ["acme/shop"]


def test_the_recorded_default_branch_wins_over_the_guess(repo: Path):
    # A clone that knows its default branch is trunk must not treat main as one
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert propose_names(str(repo)) == ["acme/shop/main", "acme/shop"]

    git(repo, "checkout", "-q", "-b", "trunk")
    assert propose_names(str(repo)) == ["acme/shop"]


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/shop.git",
        "git@github.com:acme/shop",
        "ssh://git@github.com/acme/shop.git",
        "ssh://git@github.com:2222/acme/shop.git",
        "https://github.com/acme/shop.git",
        "https://user@github.com/acme/shop",
        "https://github.com/acme/shop/",
    ],
)
def test_every_spelling_of_a_remote_gives_the_same_name(repo: Path, url: str):
    git(repo, "remote", "set-url", "origin", url)
    assert propose_names(str(repo)) == ["acme/shop"]


def test_nested_groups_are_kept_whole(repo: Path):
    git(repo, "remote", "set-url", "origin", "https://gitlab.com/org/subgroup/team/repo.git")
    assert propose_names(str(repo)) == ["org/subgroup/team/repo"]


def test_a_remote_that_is_a_local_path_is_named_by_its_last_segment(repo: Path):
    # /srv/git/thing is a place on a disk, not a group and a repository
    git(repo, "remote", "set-url", "origin", "/srv/git/thing.git")
    assert propose_names(str(repo)) == ["thing"]


def test_origin_is_preferred_over_the_other_remotes(repo: Path):
    git(repo, "remote", "add", "aaa", "git@github.com:someone/fork.git")
    assert propose_names(str(repo)) == ["acme/shop"]


def test_a_single_remote_is_used_whatever_it_is_called(repo: Path):
    git(repo, "remote", "rename", "origin", "gitlab")
    assert propose_names(str(repo)) == ["acme/shop"]


def test_without_an_origin_the_first_remote_by_name_is_used(repo: Path):
    git(repo, "remote", "rename", "origin", "zulu")
    git(repo, "remote", "add", "alpha", "git@github.com:acme/first.git")
    assert propose_names(str(repo)) == ["acme/first"]


def test_a_linked_worktree_is_named_after_its_branch(repo: Path, tmp_path: Path):
    # The worktree directory name is a local habit; the branch is what the other machine knows
    worktree = (tmp_path / "fp-feature").resolve()
    git(repo, "worktree", "add", "-q", "-b", "alice/feature", str(worktree))
    assert propose_names(str(worktree)) == ["acme/shop/feature", "acme/shop/alice/feature", "acme/shop"]


def test_without_a_remote_the_repository_directory_is_the_whole_name(tmp_path: Path):
    root = (tmp_path / "notes-cli").resolve()
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "commit", "-q", "--allow-empty", "-m", "init")
    assert propose_names(str(root)) == ["notes-cli"]


def test_without_a_remote_a_branch_is_still_appended(tmp_path: Path):
    root = (tmp_path / "notes-cli").resolve()
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "commit", "-q", "--allow-empty", "-m", "init")
    git(root, "checkout", "-q", "-b", "alice/some-work")
    assert propose_names(str(root)) == [
        "notes-cli/some-work",
        "notes-cli/alice/some-work",
        "notes-cli",
    ]


def test_a_repository_without_commits_proposes_the_repository_alone(tmp_path: Path):
    # rev-parse fails on an unborn branch and still prints "HEAD" on stdout. The bash tmux-here
    # this is ported from let that through and proposed a session called HEAD; the exit code is
    # read here instead, so an empty repository is named the way a detached HEAD is.
    root = (tmp_path / "empty").resolve()
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    assert propose_names(str(root)) == ["empty"]
