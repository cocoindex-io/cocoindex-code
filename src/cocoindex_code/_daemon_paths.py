"""Daemon filesystem paths and connection helpers.

Lightweight module with no cocoindex dependency so that the CLI client
can import these without pulling in the full daemon stack.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TypeAlias

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


def daemon_state_dir() -> Path:
    """Return the durable daemon-owned state directory.

    This is separate from both project checkout state and daemon runtime files:
    it stores shared layer metadata, materialized layer sources, and layer
    databases.  ``COCOINDEX_CODE_STATE_DIR`` exists mostly for tests and
    advanced users; otherwise we follow XDG data-home on Unix-like systems.
    """
    override = os.environ.get("COCOINDEX_CODE_STATE_DIR")
    if override:
        return Path(override)
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "cocoindex-code"
    return Path.home() / ".local" / "share" / "cocoindex-code"


DaemonAddress: TypeAlias = str | tuple[str, int]


def connection_family() -> str:
    """Return the multiprocessing connection family for this platform."""
    if os.environ.get("COCOINDEX_CODE_DAEMON_TCP"):
        return "AF_INET"
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def daemon_socket_path() -> DaemonAddress:
    """Return the daemon socket/pipe address."""
    tcp = os.environ.get("COCOINDEX_CODE_DAEMON_TCP")
    if tcp:
        host, _, port = tcp.partition(":")
        return (host or "127.0.0.1", int(port or "8765"))
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
