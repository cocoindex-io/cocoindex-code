"""Daemon filesystem paths and connection helpers.

Lightweight module with no cocoindex dependency so that the CLI client
can import these without pulling in the full daemon stack.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .settings import user_settings_dir


def daemon_runtime_dir() -> Path:
    """Return the directory that holds daemon runtime artifacts.

    Holds ``daemon.sock``, ``daemon.pid``, ``daemon.log``. Kept separate from
    the user-settings dir so that (e.g. in Docker) the socket can live on the
    container's native filesystem while ``global_settings.yml`` lives on a
    bind mount.

    Override with ``COCOINDEX_CODE_RUNTIME_DIR``. Defaults to
    :func:`user_settings_dir` for backward compatibility — non-Docker users
    see identical behavior to before the split.
    """
    override = os.environ.get("COCOINDEX_CODE_RUNTIME_DIR")
    if override:
        return Path(override)
    return user_settings_dir()


def connection_family() -> str:
    """Return the multiprocessing connection family for this platform."""
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def daemon_socket_path() -> str:
    """Return the daemon socket/pipe address."""
    if sys.platform == "win32":
        import hashlib

        # Hash the runtime dir so COCOINDEX_CODE_RUNTIME_DIR (or the
        # COCOINDEX_CODE_DIR fallback) overrides produce unique pipe names,
        # preventing conflicts between different daemon instances (tests,
        # users, etc.)
        dir_hash = hashlib.md5(str(daemon_runtime_dir()).encode()).hexdigest()[:12]
        return rf"\\.\pipe\cocoindex_code_{dir_hash}"
    return str(daemon_runtime_dir() / "daemon.sock")


def daemon_pid_path() -> Path:
    """Return the path for the daemon's PID file."""
    return daemon_runtime_dir() / "daemon.pid"


def daemon_log_path() -> Path:
    """Return the path for the daemon's log file."""
    return daemon_runtime_dir() / "daemon.log"
