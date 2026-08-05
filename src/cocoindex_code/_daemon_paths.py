"""Daemon filesystem paths and connection helpers.

Lightweight module with no cocoindex dependency so that the CLI client
can import these without pulling in the full daemon stack.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .settings import user_settings_dir

# Max bytes in sockaddr_un.sun_path, including the NUL terminator: 104 on the
# BSDs (incl. macOS), 108 on Linux. Exceeding it makes bind() fail with
# "AF_UNIX path too long" — unlike ordinary files, which get PATH_MAX (~1024).
_SUN_PATH_MAX = 104 if sys.platform == "darwin" else 108


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


def _runtime_dir_hash() -> str:
    """Stable short id for the current runtime dir.

    Distinguishes daemon instances that differ only by
    ``COCOINDEX_CODE_RUNTIME_DIR`` / ``COCOINDEX_CODE_DIR``, so tests, users,
    and containers never collide on one address.
    """
    return hashlib.md5(str(daemon_runtime_dir()).encode()).hexdigest()[:12]


def daemon_socket_path() -> str:
    """Return the daemon socket/pipe address.

    Normally ``<runtime dir>/daemon.sock``. When that exceeds the platform's
    ``sun_path`` limit — a deep ``$HOME``, a sandbox, a container path — it
    would fail at bind() with a message that reads like a broken install, so
    fall back to a short path under the temp dir keyed by the runtime dir.
    Client and daemon both resolve through here, so they agree either way.
    """
    if sys.platform == "win32":
        return rf"\\.\pipe\cocoindex_code_{_runtime_dir_hash()}"

    path = daemon_runtime_dir() / "daemon.sock"
    if len(str(path).encode()) < _SUN_PATH_MAX:
        return str(path)

    # gettempdir() honors $TMPDIR, which is per-user and private on macOS; on
    # Linux it is usually shared /tmp, so include the uid to avoid a foreign
    # socket sitting at our address.
    short = Path(tempfile.gettempdir()) / f"ccc-{os.getuid()}-{_runtime_dir_hash()}.sock"
    return str(short)


def daemon_pid_path() -> Path:
    """Return the path for the daemon's PID file."""
    return daemon_runtime_dir() / "daemon.pid"


def daemon_log_path() -> Path:
    """Return the path for the daemon's log file."""
    return daemon_runtime_dir() / "daemon.log"


def daemon_last_exit_path() -> Path:
    """Return the path for the daemon's graceful-exit marker file."""
    return daemon_runtime_dir() / "last_exit"


@dataclass
class LastExitMarker:
    """Record of the most recent *graceful* daemon exit.

    Written by the daemon's graceful shutdown path (``StopRequest``,
    SIGTERM/SIGINT) and removed again at the next daemon startup. A crashed
    daemon (SIGKILL, OOM, segfault) never writes it — that absence is how the
    client tells a crash from a graceful exit.
    """

    pid: int
    reason: str
    timestamp: float


def write_last_exit_marker(pid: int, reason: str) -> None:
    """Persist the graceful-exit marker. Best-effort — never raises."""
    payload = json.dumps({"pid": pid, "reason": reason, "timestamp": time.time()})
    try:
        daemon_last_exit_path().write_text(payload)
    except OSError:
        pass


def read_last_exit_marker() -> LastExitMarker | None:
    """Read the graceful-exit marker, or None when absent/unreadable."""
    try:
        data = json.loads(daemon_last_exit_path().read_text())
        return LastExitMarker(
            pid=int(data["pid"]),
            reason=str(data["reason"]),
            timestamp=float(data["timestamp"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_last_exit_marker() -> None:
    """Remove the marker (daemon startup) so it always refers to the most recent exit."""
    daemon_last_exit_path().unlink(missing_ok=True)
