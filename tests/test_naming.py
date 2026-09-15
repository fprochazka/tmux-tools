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
    return root


def test_outside_a_git_repository_there_are_no_proposals(tmp_path: Path):
    assert propose_names(str(tmp_path.resolve())) == []


def test_branch_then_repository_directory(repo: Path):
    assert propose_names(str(repo)) == ["personal/main", "personal/acme-shop"]


def test_the_owner_segment_of_a_branch_is_offered_stripped_first(repo: Path):
    git(repo, "checkout", "-q", "-b", "fp/some-work")
    assert propose_names(str(repo)) == ["personal/some-work", "personal/fp/some-work", "personal/acme-shop"]


def test_a_detached_head_carries_no_branch_name(repo: Path):
    git(repo, "checkout", "-q", "--detach", "HEAD")
    assert propose_names(str(repo)) == ["personal/acme-shop"]


def test_a_branch_named_after_the_repository_is_proposed_once(repo: Path):
    git(repo, "checkout", "-q", "-b", "acme-shop")
    assert propose_names(str(repo)) == ["personal/acme-shop"]


def test_a_linked_worktree_is_named_after_the_main_repository(repo: Path, tmp_path: Path):
    worktree = (tmp_path / "wt").resolve()
    git(repo, "worktree", "add", "-q", "-b", "fp/feature", str(worktree))
    assert propose_names(str(worktree)) == ["personal/feature", "personal/fp/feature", "personal/acme-shop"]


def test_the_prefix_is_the_first_directory_under_the_projects_dir(tmp_path: Path):
    projects = (tmp_path / "projects").resolve()
    repo = projects / "acme" / "shop"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    assert propose_names(str(repo), projects) == ["acme/main", "acme/shop"]


def test_the_projects_dir_itself_keeps_the_default_prefix(tmp_path: Path):
    projects = (tmp_path / "projects").resolve()
    projects.mkdir()
    git(projects, "init", "-q", "-b", "main")
    git(projects, "commit", "-q", "--allow-empty", "-m", "init")
    assert propose_names(str(projects), projects) == ["personal/main", "personal/projects"]


def test_a_repository_without_commits_proposes_head(tmp_path: Path):
    # Inherited from the bash tmux-here: rev-parse fails there but still prints HEAD on stdout,
    # so the failed call leaves a branch name behind. Locked in on purpose, not endorsed.
    root = (tmp_path / "empty").resolve()
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    assert propose_names(str(root)) == ["personal/HEAD"]
