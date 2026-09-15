# tmux-tools

Command line helpers for tmux, shipped as one Python package that installs several commands. So far there is one: `tmux-here`.

## tmux-here

Attach to the tmux session that already runs in the directory you are standing in.

### Why

One session per project and per worktree adds up fast. `tmux ls` lists session names, not directories, so finding the session that belongs to the directory you just changed into means matching paths by eye. `tmux-here` does that match. When nothing matches, it proposes the name you would have typed anyway and creates the session.

### What it does

A session matches when any of its panes has a current path equal to the current directory or below it.

- One match: attach to it.
- Several matches: a numbered list, then a number to pick one. The session you are in is marked `[current]`.
- No match: the proposed names, then a number or a name of your own, then the new session.

Inside tmux it switches the client instead of attaching, and creates a new session detached before switching to it, so you never end up with a tmux nested inside a tmux.

Outside tmux, `tmux-here` replaces itself with the `tmux` process rather than running it as a child. Nothing of it is left sitting between your shell and the session.

### Proposed names

A proposal is `<prefix>/<name>`.

The prefix is the first path segment below `~/devel/projects` when the current directory is under it, and `personal` when it is not. That directory is hardcoded; there is no setting for it.

The name comes from git, best guess first:

1. the checked out branch with its owner segment removed, so `fp/some-work` gives `some-work`
2. the full branch
3. the directory name of the repository

Inside a linked worktree the third proposal names the main repository rather than the worktree directory, so every worktree of a repository proposes the same fallback name. On a detached HEAD the two branch proposals are gone. Outside a git repository there are no proposals at all and the prompt asks for a plain name.

At the prompt a number picks a proposal, and anything else becomes the session name as typed. A name some other session already holds is refused, and the prompt comes back.

### Requirements

- tmux on your PATH. `tmux-here` reports it as missing and stops.
- git, only for the name proposals. Without it you get the prompt with no proposals.

A tmux server that is not running yet is not an error. It means no session matches, so the creation flow runs.

### Exit codes

`0` when a session was attached, switched to, or created, and when you were already in the session that matched. `1` when tmux is missing, when the selection is not a number or out of range, and on end of input at a prompt. `2` on a bad command line. Ctrl+C leaves with `130` and prints nothing.

## Installation

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

There is no PyPI release, so install from a clone as an editable uv tool. The commands land on your PATH, and edits in the clone take effect with no reinstall.

1. Clone the repository and enter it:

   ```bash
   git clone https://github.com/fprochazka/tmux-tools.git
   cd tmux-tools
   ```

2. Install the tools:

   ```bash
   uv tool install --editable .
   ```

3. Check it:

   ```bash
   tmux-here --help
   ```

To upgrade, pull the clone and reinstall:

```bash
git pull
uv tool install --editable . --reinstall
```

To remove it, run `uv tool uninstall tmux-tools`.

## Project structure

```
src/tmux_tools/
├── __init__.py       # package version
├── entrypoint.py     # the bootstrap every command runs first, and the exec handover
├── console.py        # the stdout and stderr consoles, and the prompt
├── tmux.py           # the tmux binary, and matching panes against a directory
├── naming.py         # session names proposed from git and the project layout
└── commands/
    └── here.py       # the `tmux-here` command

tests/                # unit tests for naming and pane matching
```

A new tool is a new module under `commands/` plus a line in `[project.scripts]`.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

Run a command from the clone without installing it with `uv run tmux-here`.

## License

MIT
