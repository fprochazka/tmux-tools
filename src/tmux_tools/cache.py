"""The ``session@device`` names the last full scan found, kept on disk for the shell completion."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from .config import CONFIG_DIR

# The completion scripts spell this path out on their own, so a change here is a change there.
SESSIONS_FILE = "there-sessions"


def sessions_path() -> Path:
    """Where the names of the last scan are kept, whether or not anything is there yet."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / CONFIG_DIR / SESSIONS_FILE


def save_sessions(names: list[str], path: Path | None = None) -> None:
    """Replace the kept names with these, one per line.

    The file's modification time is its age, which the completion scripts read without starting
    Python. That is also why nothing in the file says when it was written.

    The names go to a temporary file in the same directory first and are renamed over the old
    file, so a completion that reads it halfway through a scan sees the old list or the new one,
    never half of either. An empty list is written too: no session anywhere is an answer.

    A cache that cannot be written costs the completion a scan of its own and nothing else, so
    failing here is silent. ``ls`` was asked for a listing, not for a cache.
    """
    path = path or sessions_path()
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.writelines(f"{name}\n" for name in names)
            os.replace(temporary, path)
        except BaseException:
            os.unlink(temporary)
            raise
