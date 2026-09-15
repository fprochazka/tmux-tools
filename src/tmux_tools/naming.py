"""Session names worth offering for a directory, derived from the git remote and the branch."""

from __future__ import annotations

import os
import subprocess

# Branches to treat as the default one when the clone never learned which it is.
_ASSUMED_DEFAULT_BRANCHES = ("master", "main")


def _git(args: list[str], cwd: str) -> tuple[int, str]:
    """Run git in ``cwd`` and return its exit code with its trimmed stdout.

    Every call here is a question that may have no answer, so git's own complaints are dropped:
    a directory outside any repository would otherwise print a fatal error before the prompt.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return 127, ""
    return result.returncode, result.stdout.rstrip("\n")


def propose_names(cwd: str) -> list[str]:
    """Names to offer for a new session in ``cwd``, best guess first, already deduplicated.

    A name is a repository and a branch, such as ``org/team/repo/new-checkout``. The repository
    half is the path of the git remote with the host and any trailing ``.git`` gone and every
    group segment kept, because GitLab groups nest and the leaf name alone is not the
    repository. It comes from ``origin``; without an ``origin``, from the only remote, or from
    the first by name, so that one repository proposes one name on every run. A repository with
    no remote falls back to the name of its directory, and nothing stands in for the group path
    it does not have.

    The remote is what two clones of a repository agree on, and the directory one of them
    happens to sit in is not. A worktree checked out in ``alice-new-checkout`` and the main
    checkout next to it both lead back to ``org/team/repo``, so the names line up across
    machines without anyone arranging their directories the same way.

    The branch half follows, offered without its owner segment first, since ``alice/new-checkout``
    is named after whoever is reading the prompt, then in full, and last the repository on its
    own. On the default branch there is no branch segment at all, because ``org/team/repo/master``
    says nothing that ``org/team/repo`` does not. Which branch that is comes from the default the
    clone recorded, and from treating ``master`` and ``main`` as default when it recorded none;
    the server is never asked, because this runs on every tmux-here in a directory with no
    session, including on a train. A detached HEAD and a repository without commits have no
    branch to name anything after, so both propose the repository alone.

    Empty outside a git repository, where there is nothing to build a name from.
    """
    repo_root = _repo_root(cwd)
    if not repo_root:
        return []

    base = _remote_base(cwd) or os.path.basename(repo_root)
    branch = _branch(cwd)

    candidates: list[str] = []
    if branch and not _is_default_branch(cwd, branch):
        # Branches carry an owner segment (alice/some-work) that adds nothing here
        if "/" in branch:
            candidates.append(f"{base}/{branch.split('/', 1)[1]}")
        candidates.append(f"{base}/{branch}")
    candidates.append(base)

    return list(dict.fromkeys(candidates))


def _repo_root(cwd: str) -> str:
    """The directory of the repository ``cwd`` belongs to, empty when it belongs to none.

    A linked worktree resolves to the main repository, so that a repository with no remote
    proposes one name from all of its worktrees rather than one per worktree directory.
    """
    status, common_dir = _git(["rev-parse", "--git-common-dir"], cwd)
    if status != 0:
        return ""
    if common_dir.startswith("/"):
        return os.path.dirname(common_dir)

    status, top_level = _git(["rev-parse", "--show-toplevel"], cwd)
    return top_level if status == 0 else ""


def _branch(cwd: str) -> str:
    """The checked out branch, empty on a detached HEAD and in a repository with no commits.

    Neither case has a branch to name a session after. An unborn branch makes rev-parse fail
    and still print "HEAD" on stdout, which is why the exit code decides here and not the text.
    """
    status, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if status != 0 or branch == "HEAD":
        return ""
    return branch


def _is_default_branch(cwd: str, branch: str) -> bool:
    """Whether ``branch`` is the default one of the repository ``cwd`` belongs to.

    ``refs/remotes/origin/HEAD`` is what a clone writes down once and what many clones never
    have at all. A wrong guess from the fallback costs one extra segment in a proposed name,
    which is the cheaper of the two mistakes available here.
    """
    status, head = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd)
    if status == 0 and "/" in head:
        return branch == head.split("/", 1)[1]
    return branch in _ASSUMED_DEFAULT_BRANCHES


def _remote_base(cwd: str) -> str:
    """The remote's path, empty when the repository has no remote or none with a usable URL."""
    status, listed = _git(["remote"], cwd)
    if status != 0:
        return ""

    remotes = sorted(name for name in listed.splitlines() if name)
    if not remotes:
        return ""

    # Several remotes and no origin still has to name the same session on every run
    chosen = "origin" if "origin" in remotes else remotes[0]
    status, url = _git(["remote", "get-url", chosen], cwd)
    return _path_of(url) if status == 0 else ""


def _path_of(url: str) -> str:
    """The group path and repository name in a remote URL, without the host or a ``.git`` tail.

    Every spelling of the same remote has to reduce to the same name, so ``git@host:org/repo.git``,
    ``ssh://git@host:2222/org/repo.git`` and ``https://user@host/org/repo`` all give ``org/repo``.
    Group nesting is kept whole, because GitLab groups nest as deep as they like and the leaf
    name alone is not the repository.
    """
    url = url.strip().rstrip("/")
    scheme, separator, rest = url.partition("://")

    if separator and scheme != "file":
        # The authority is the first segment, so a user name and a port fall away with the host
        path = rest.partition("/")[2]
    elif not separator and ":" in url and "/" not in url.partition(":")[0]:
        # scp-like git@host:org/repo.git, where a colon ahead of any slash marks the host
        path = url.partition(":")[2]
    else:
        # A path on this machine has no host to strip, and "srv/git/thing" names nothing
        path = os.path.basename(rest if separator else url)

    return path.strip("/").removesuffix(".git").rstrip("/")
