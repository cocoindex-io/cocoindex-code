"""Daemon filesystem paths and connection helpers.

Lightweight module with no cocoindex dependency so that the CLI client
can import these without pulling in the full daemon stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .settings import user_settings_dir


def daemon_dir() -> Path:
    """Return the daemon directory (``~/.cocoindex_code/``)."""
    return user_settings_dir()


def connection_family() -> str:
    """Return the multiprocessing connection family for this platform."""
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def daemon_socket_path() -> str:
    """Return the daemon socket/pipe address."""
    if sys.platform == "win32":
        import hashlib

        # Hash the daemon dir so COCOINDEX_CODE_DIR overrides create unique pipe names,
        # preventing conflicts between different daemon instances (tests, users, etc.)
        dir_hash = hashlib.md5(str(daemon_dir()).encode()).hexdigest()[:12]
        return rf"\\.\pipe\cocoindex_code_{dir_hash}"
    return str(daemon_dir() / "daemon.sock")


def daemon_pid_path() -> Path:
    """Return the path for the daemon's PID file."""
    return daemon_dir() / "daemon.pid"


def daemon_log_path() -> Path:
    """Return the path for the daemon's log file."""
    return daemon_dir() / "daemon.log"
