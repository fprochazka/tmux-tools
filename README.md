# tmux-tools

Command line helpers for tmux, shipped as one Python package that installs several commands: `tmux-here` for the session in the directory you are standing in, and `tmux-there` for the sessions on the other machines on your tailnet.

## tmux-here

Attach to the tmux session that already runs in the directory you are standing in.

**Why?** One session per project and per worktree adds up fast, and `tmux ls` lists session names rather than directories, so finding the one that belongs to the directory you just changed into means matching paths by eye.

A session matches when any of its panes has a current path equal to the current directory or below it. With one match `tmux-here` attaches to it, and with several it asks:

```
$ cd ~/code/acme/shop
$ tmux-here

Sessions in /home/you/code/acme/shop:

   1) acme/shop [current]
      /home/you/code/acme/shop
   2) acme/shop/new-checkout
      /home/you/code/acme/shop/.worktrees/new-checkout

Enter number to attach, or a new session name (Ctrl+C to cancel):
```

With no match it proposes names instead:

```
$ cd ~/code/acme/shop
$ git remote get-url origin
git@gitlab.example.com:acme/shop.git
$ git branch --show-current
alice/new-checkout
$ tmux-here

No tmux session runs in /home/you/code/acme/shop.

   1) acme/shop/new-checkout
   2) acme/shop/alice/new-checkout
   3) acme/shop

Enter number or a new session name (Ctrl+C to cancel):
```

Another repository:

```
$ cd ~/code/scratchpad
$ git remote
$ tmux-here

No tmux session runs in /home/you/code/scratchpad.

   1) scratchpad

Enter number or a new session name (Ctrl+C to cancel):
```

Both prompts answer to the same two things. A number picks a line, anything else becomes the name of a new session started in the current directory, and a name some other session already holds is refused so that the prompt comes back. A directory with two sessions in it can want a third.

Inside tmux, `tmux-here` switches the client instead of attaching, and creates a new session detached before switching to it, so you never end up with a tmux nested inside a tmux. Outside tmux it replaces itself with the `tmux` process rather than running it as a child. It needs tmux on your PATH, and git only for the proposals.

## tmux-there

Attach to a tmux session on any machine on your Tailscale tailnet.

**Why?** Long-lived sessions live on a machine you are not sitting at, and reaching one means ssh to the host, `tmux ls`, read the names, `tmux attach -t <name>`, and the same again after every drop.

```
$ tmux-there

workshop (local)
   1) notes
      /home/you/notes

attic
   2) acme/shop
      /home/you/code/acme/shop
   3) acme/shop/new-checkout
      /home/you/code/acme/shop/.worktrees/new-checkout

Enter number to attach (Ctrl+C to cancel):
```

The numbering runs in one sequence across the devices, this machine first and the others by name, so the answer is a single number.

The devices come from `tailscale status --json`: every one that is online and is not a phone or a tablet. A device that answers with nothing is not listed at all, because no tmux installed, no tmux server running and a refused connection are the same answer. Without a tailnet to read, the listing is this machine alone.

A remote pick hands the terminal to `ssh -t <host> tmux attach -t <name>` and replaces `tmux-there` with it. The attach carries no `-d`, so a session another client already holds stays with it. A local pick behaves like `tmux-here`.

Tailscale SSH asks for a browser login every so often, in the middle of a scan:

```
$ tmux-there
attic: To authenticate, visit: https://login.tailscale.com/a/1f2e3d4c5b6a
```

Visit it and the same connection carries on and produces its listing. The probe waiting on you keeps no deadline, because no connect timeout is long enough for a person walking to a browser, so Ctrl+C is the way out.

Reaching the other machines takes tailscale running and logged in, ssh on your PATH, Tailscale SSH set up for the tailnet, and tmux on the other side. None of it is asked for until a machine is about to be read.

Without a config file every online device is scanned, which is how this is meant to be run. To narrow it, put a file at `$XDG_CONFIG_HOME/fprochazka-tmux-tools/config.toml`, or at `~/.config/fprochazka-tmux-tools/config.toml`:

```toml
[devices]
only = ["workshop", "attic"]
# skip = ["cache", "runner"]
```

`only` is the whole list of devices to scan and `skip` takes devices out of it; with both in the file, `only` is used and `skip` is ignored. The names are the host names `tailscale status` shows, matched exactly.

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

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

Run a command from the clone without installing it with `uv run tmux-here`.

```
src/tmux_tools/
├── __init__.py       # package version
├── entrypoint.py     # the bootstrap every command runs first, and the exec handover
├── console.py        # the stdout and stderr consoles, and the prompt
├── config.py         # the optional config file
├── tmux.py           # the tmux binary, here or behind ssh, and matching panes against a directory
├── tailnet.py        # the devices on the tailnet, and reading their sessions over ssh
├── naming.py         # session names proposed from the git remote and the branch
└── commands/
    ├── here.py       # the `tmux-here` command
    └── there.py      # the `tmux-there` command

tests/                # unit tests for naming, pane matching, device selection, the prompts and the config file
```

A new tool is a new module under `commands/` plus a line in `[project.scripts]`, and why a rule is the way it is lives in the docstrings.

MIT licensed.
