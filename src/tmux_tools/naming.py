"""Session names worth offering for a directory, derived from git and the project layout."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECTS_DIR = Path.home() / "devel" / "projects"


def _git(args: list[str], cwd: str, *, quiet: bool = False) -> tuple[int, str]:
    """Run git in ``cwd`` and return its exit code with its trimmed stdout."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL if quiet else None,
            text=True,
            check=False,
        )
    except OSError:
        return 127, ""
    return result.returncode, result.stdout.rstrip("\n")


def propose_names(cwd: str, projects_dir: Path = PROJECTS_DIR) -> list[str]:
    """Names to offer for a new session in ``cwd``, best guess first, already deduplicated.

    Empty outside a git repository, where there is nothing to build a name from.
    """
    repo_root = ""
    status, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, quiet=True)
    if status == 0:
        # A detached HEAD carries no useful name
        if branch == "HEAD":
            branch = ""

        _, common_dir = _git(["rev-parse", "--git-common-dir"], cwd)
        if common_dir.startswith("/"):
            # Inside a linked worktree - name the main repository, not the worktree
            repo_root = os.path.dirname(common_dir)
        else:
            _, repo_root = _git(["rev-parse", "--show-toplevel"], cwd)

    # A repository without a single commit is the one case where git fails and still prints
    # "HEAD" on stdout, so the branch survives the failed call and the only proposal becomes
    # "<prefix>/HEAD". The bash tmux-here this is ported from does the same; leave it alone.
    if not branch and not repo_root:
        return []

    prefix = "personal"
    under_projects = f"{projects_dir}/"
    if cwd.startswith(under_projects):
        prefix = cwd[len(under_projects) :].split("/", 1)[0]

    candidates: list[str] = []
    if branch:
        # Branches carry an owner segment (fp/some-work) that adds nothing here
        if "/" in branch:
            candidates.append(f"{prefix}/{branch.split('/', 1)[1]}")
        candidates.append(f"{prefix}/{branch}")
    if repo_root:
        candidates.append(f"{prefix}/{os.path.basename(repo_root)}")

    return list(dict.fromkeys(candidates))
