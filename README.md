# tmux-tools

Command line helpers for tmux, shipped as one Python package that installs several commands: `tmux-here` for the session in the directory you are standing in, and `tmux-there` for the sessions on the other machines on your tailnet.

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

## tmux-there

Attach to a tmux session on any machine on your Tailscale tailnet.

### Why

A dozen long-lived sessions sit on the machine at home and you are on a train with a laptop. Reaching one of them means ssh to the host, `tmux ls`, read the names, `tmux attach -t <name>`, and the same again after every drop. `tmux-there` is that sequence as one command and one number.

It does nothing about the drops. Whatever carries the connection, ssh, mosh or Eternal Terminal, is a separate question; what this adds is that getting back takes one keystroke.

### What it does

It reads the tailnet from `tailscale status --json` and scans every device that is online and is not a phone or a tablet. This machine is scanned too, through the tmux binary, with no ssh in the way.

The sessions are printed grouped by device and numbered in one sequence across all of them, so the answer is a single number. The session you are in is marked `[current]`.

This machine comes first and the other machines follow, by name, so that the list ends right above the prompt with the numbers you are most likely to type. A local session is what `tmux-here` is for.

A remote pick hands the terminal to `ssh -t <host> tmux attach -t <name>` and replaces `tmux-there` with it, so nothing of it is left between your shell and a three hour session. A local pick behaves like `tmux-here`: switch the client inside tmux, attach outside it.

The attach carries no `-d`. A session another client already holds stays with it, because the desktop at home keeps ten of them open and an attach from the road should not throw any of them off.

Devices that answer with nothing are not listed at all. No tmux installed, no tmux server running and a refused connection are the same answer, and a tailnet with service nodes on it would otherwise say so on every run. A device `tailscale status` calls online can still be unreachable from where you are, so the scan keeps a wall clock of its own and gives up on the stragglers.

Without a tailnet to read, the listing is this machine alone, named by its system host name. A laptop whose tailscaled is stopped, one that is logged out, and one that never had tailscale on it all still have sessions worth attaching to. Tailscale that is not installed is passed over in silence, because nothing is wrong. Tailscale that is installed and cannot answer gets one line saying so, and whatever tailscale itself printed, so you know why the other machines are missing.

### Configuration

There is none to write. Without a config file every online device is scanned, which is how this is meant to be run.

To narrow it, put a file at `$XDG_CONFIG_HOME/fprochazka-tmux-tools/config.toml`, or at `~/.config/fprochazka-tmux-tools/config.toml`:

```toml
[devices]
only = ["fprochazka-wolverine", "fprochazka-fatgrandpa"]
# skip = ["searxng", "mcphub"]
```

`only` is the whole list of devices to scan. `skip` takes devices out of it. With both in the file, `only` is used and `skip` is ignored. The names are the host names `tailscale status` shows, matched exactly. A name that matches nothing gets a warning and the scan goes on. A file that cannot be parsed stops the command.

### Tailscale SSH authentication

Tailscale SSH asks for a browser login every so often. The connection stops there and the remote side prints a `https://login.tailscale.com/...` URL.

Each probe is read as it runs, so that URL reaches you the moment it appears, labelled with the host it came from. Visit it, and the same connection carries on and produces its listing. Nothing is restarted, and the probe waiting on you keeps no deadline, because no connect timeout is long enough for a person walking to a browser. Ctrl+C is the way out.

The check is per tailnet rather than per machine, so one device is probed on its own first and the rest follow a few seconds later. That is what keeps eight hosts from asking you the same question eight times.

### Requirements

tmux on your PATH is all it takes to list and attach to the sessions on this machine. Without it `tmux-there` reports it as missing and stops.

The rest is what it takes to reach the other machines, and none of it is asked for until one is about to be read:

- tailscale, running and logged in. The tailnet is where the device list comes from.
- ssh on your PATH.
- Tailscale SSH set up for the tailnet. Devices are reached by their MagicDNS name with no user name in front, and identity comes from Tailscale.
- tmux on the other machines, or they have nothing to list.

### Exit codes

`0` when a session was attached or switched to, and when you picked the session you are already in. `1` when tmux is missing, when ssh is missing and another machine has to be read, when the config file cannot be parsed, when no machine has a session to attach to, when the selection is not a number or out of range, and on end of input at the prompt. `2` on a bad command line. Ctrl+C leaves with `130` and prints nothing.

A tailnet that cannot be read is not an exit code. It costs you the other machines and nothing else.

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
   tmux-there --help
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
├── config.py         # the optional config file
├── tmux.py           # the tmux binary, here or behind ssh, and matching panes against a directory
├── tailnet.py        # the devices on the tailnet, and reading their sessions over ssh
├── naming.py         # session names proposed from git and the project layout
└── commands/
    ├── here.py       # the `tmux-here` command
    └── there.py      # the `tmux-there` command

tests/                # unit tests for naming, pane matching, device selection, the picker and the config file
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
