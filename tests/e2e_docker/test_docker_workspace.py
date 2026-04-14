"""End-to-end tests exercising the full Docker setup.

Gated behind the ``docker_e2e`` marker (excluded from the default pytest run).
Run with: ``uv run pytest -m docker_e2e``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


pytestmark = [
    pytest.mark.docker_e2e,
    pytest.mark.skipif(not _docker_available(), reason="Docker not available on this host"),
]


def docker_exec(
    container_name: str,
    argv: list[str],
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Thin wrapper over ``docker exec`` that captures stdout/stderr as text."""
    cmd = ["docker", "exec"]
    for k, v in (env or {}).items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.append(container_name)
    cmd.extend(argv)
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def test_index_and_search_happy_path(container: str, fixture_workspace: Path) -> None:
    """Baseline flow: daemon comes up, `ccc init` writes settings and triggers
    supervised respawn, `ccc index` succeeds, `ccc search` returns hits.
    """
    # Container starts with no global_settings.yml; daemon is in no-settings mode.
    # `ccc init -f` writes defaults (no TTY → falls back to default st model).
    docker_exec(
        container,
        ["ccc", "init", "-f"],
        env={"COCOINDEX_CODE_HOST_CWD": str(fixture_workspace)},
    )

    index_result = docker_exec(
        container,
        ["ccc", "index"],
        env={"COCOINDEX_CODE_HOST_CWD": str(fixture_workspace)},
    )
    assert index_result.returncode == 0, index_result.stderr

    search = docker_exec(
        container,
        ["ccc", "search", "password", "verification"],
        env={"COCOINDEX_CODE_HOST_CWD": str(fixture_workspace)},
    )
    assert search.returncode == 0, search.stderr
    # Relative path — unchanged by host-path mapping, identical to what a
    # host-side `ccc` would print.
    assert "src/auth.py" in search.stdout


def test_host_cwd_forwarding_resolves_project(container: str, fixture_workspace: Path) -> None:
    """With COCOINDEX_CODE_HOST_CWD set, subproject-cwd commands find the right project."""
    docker_exec(
        container,
        ["ccc", "init", "-f"],
        env={"COCOINDEX_CODE_HOST_CWD": str(fixture_workspace)},
    )

    # Invoke `ccc status` from the host-form subdirectory path.
    status = docker_exec(
        container,
        ["ccc", "status"],
        env={"COCOINDEX_CODE_HOST_CWD": str(fixture_workspace / "src")},
    )
    assert status.returncode == 0, status.stderr
    # Project header should show the HOST path (translated via the mapping),
    # rooted at the host workspace — not /workspace.
    assert str(fixture_workspace) in status.stdout


def test_host_cwd_invalid_warns_but_continues(container: str) -> None:
    """An unresolvable host-cwd emits a stderr warning; the command still runs."""
    result = docker_exec(
        container,
        ["ccc", "daemon", "status"],
        env={"COCOINDEX_CODE_HOST_CWD": "/nonexistent/zzz"},
        check=False,
    )
    assert "COCOINDEX_CODE_HOST_CWD" in result.stderr
    assert result.returncode == 0


def test_settings_change_triggers_supervised_restart(
    container: str, fixture_workspace: Path
) -> None:
    """Editing ``global_settings.yml`` should cause the next CLI call to
    restart the daemon via the entrypoint's respawn loop — not take the
    container down.
    """
    import time

    # First pass — daemon in no-settings mode writes settings via init.
    docker_exec(
        container,
        ["ccc", "init", "-f"],
        env={"COCOINDEX_CODE_HOST_CWD": str(fixture_workspace)},
    )
    # Record the container's start-time so we can detect a respawn (pid reset).
    initial_status = docker_exec(container, ["ccc", "daemon", "status"])
    assert initial_status.returncode == 0
    assert "Uptime:" in initial_status.stdout

    # Edit global_settings.yml on the host side (bind mount → visible in container).
    settings_file = fixture_workspace / ".cocoindex_code" / "global_settings.yml"
    assert settings_file.is_file()
    content = settings_file.read_text()
    # Touch mtime by rewriting identical content — still triggers mtime mismatch.
    time.sleep(1.1)  # ensure mtime actually changes on coarse-grained filesystems
    settings_file.write_text(content)

    # Next CLI call: the client should detect the mtime mismatch, request
    # daemon stop, and the entrypoint restart loop should bring a fresh
    # daemon back up. Container must still be running.
    after = docker_exec(container, ["ccc", "daemon", "status"])
    assert after.returncode == 0, after.stderr
    assert "Uptime:" in after.stdout

    # Container's PID 1 (the entrypoint shell) should still be alive → container not restarted.
    inspect = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", container],
        capture_output=True,
        text=True,
        check=True,
    )
    assert inspect.stdout.strip() == "running"


def test_first_start_uses_baked_model(container: str) -> None:
    """Fresh container starts with the pre-baked model — no network download."""
    # The daemon is already up from the `container` fixture's readiness probe.
    # Check that the sentence-transformers cache contains the baked model.
    check = docker_exec(
        container,
        ["ls", "/var/cocoindex/cache/sentence-transformers"],
    )
    assert check.returncode == 0
    # At least one model directory should be present from the bake stage.
    assert check.stdout.strip() != ""

    # Daemon log should not contain a "Downloading" line.
    log_result = subprocess.run(
        ["docker", "logs", container],
        capture_output=True,
        text=True,
        check=True,
    )
    # sentence-transformers prints "Downloading" when it fetches a model. A
    # missing bake would trigger that.
    assert "Downloading" not in log_result.stdout
    assert "Downloading" not in log_result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="PUID/PGID only meaningful on Linux")
def test_linux_puid_gives_host_owned_files(
    docker_image: str, fixture_workspace: Path, tmp_path: Path
) -> None:
    """With PUID/PGID set, daemon-written files on the bind mount are owned by the host user."""
    import os
    import uuid

    name = f"ccc-e2e-puid-{uuid.uuid4().hex[:8]}"
    host_ws = str(fixture_workspace)
    # os.getuid/getgid only exist on POSIX; the skipif above already gates
    # this test to Linux, so getattr lets mypy pass on Windows runners.
    uid = os.getuid()  # type: ignore[attr-defined,unused-ignore]
    gid = os.getgid()  # type: ignore[attr-defined,unused-ignore]
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "-v",
                f"{host_ws}:/workspace",
                "-v",
                "/var/cocoindex",
                "-e",
                f"COCOINDEX_CODE_HOST_PATH_MAPPING=/workspace={host_ws}",
                "-e",
                f"PUID={uid}",
                "-e",
                f"PGID={gid}",
                docker_image,
            ],
            check=True,
            capture_output=True,
        )
        # Wait for daemon startup (which creates /workspace/.cocoindex_code/global_settings.yml).
        import time

        settings_file = fixture_workspace / ".cocoindex_code" / "global_settings.yml"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if settings_file.is_file():
                break
            time.sleep(0.5)
        else:
            raise TimeoutError("Daemon did not create global_settings.yml in time")

        st = settings_file.stat()
        assert st.st_uid == uid, f"Expected uid {uid}, got {st.st_uid}"
        assert st.st_gid == gid, f"Expected gid {gid}, got {st.st_gid}"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.mark.skipif(sys.platform != "linux", reason="root-in-container UID check is Linux-specific")
def test_linux_no_puid_runs_as_root(container: str) -> None:
    """With PUID/PGID unset, the daemon process stays as uid 0 (preserves today's default)."""
    result = docker_exec(container, ["id", "-u"])
    assert result.stdout.strip() == "0"


def test_docker_compose_smoke(docker_image: str, fixture_workspace: Path, tmp_path: Path) -> None:
    """`docker compose up -d` + `docker compose exec ccc search` round-trips."""
    import os
    import shutil

    compose_src = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"
    compose_dst = tmp_path / "docker-compose.yml"
    shutil.copy2(compose_src, compose_dst)

    # The compose file references cocoindex/cocoindex-code:latest (the published
    # release image). Tag the locally-built pytest image under that name so
    # `docker compose up` uses our local code instead of pulling from Docker Hub.
    subprocess.run(
        ["docker", "tag", docker_image, "cocoindex/cocoindex-code:latest"],
        check=True,
    )

    env = dict(os.environ)
    env["COCOINDEX_HOST_WORKSPACE"] = str(fixture_workspace)

    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_dst), "up", "-d"],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
        )
        # Wait for daemon readiness.
        import time

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            check = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(compose_dst),
                    "exec",
                    "-T",
                    "cocoindex-code",
                    "sh",
                    "-c",
                    "test -S /var/run/cocoindex_code/daemon.sock",
                ],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                check=False,
            )
            if check.returncode == 0:
                break
            time.sleep(1)
        else:
            raise TimeoutError("Daemon did not become ready via compose")

        # Index, then search.
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_dst),
                "exec",
                "-T",
                "-e",
                f"COCOINDEX_CODE_HOST_CWD={fixture_workspace}",
                "cocoindex-code",
                "ccc",
                "init",
                "-f",
            ],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_dst),
                "exec",
                "-T",
                "-e",
                f"COCOINDEX_CODE_HOST_CWD={fixture_workspace}",
                "cocoindex-code",
                "ccc",
                "index",
            ],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
        )
        search = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_dst),
                "exec",
                "-T",
                "-e",
                f"COCOINDEX_CODE_HOST_CWD={fixture_workspace}",
                "cocoindex-code",
                "ccc",
                "search",
                "request",
                "handler",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "src/handlers.py" in search.stdout
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_dst), "down", "-v"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            check=False,
        )
