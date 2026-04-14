"""Shared fixtures for Dockerized end-to-end tests.

All tests here are gated behind the ``docker_e2e`` pytest marker AND a
``skipif not docker_available()`` so that missing Docker on the host skips
cleanly instead of failing.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
FIXTURE_PROJECT = REPO_ROOT / "tests" / "e2e_docker_fixtures" / "sample_project"


@pytest.fixture(scope="session")
def docker_image() -> str:
    """Build the image once per test session, installing cocoindex-code from the
    local source tree (not PyPI) so tests exercise the current changes. Returns the tag.
    """
    tag = "cocoindex-code:pytest"
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(DOCKERFILE),
            "--build-arg",
            "CCC_INSTALL_SPEC=/ccc-src[default]",
            "-t",
            tag,
            str(REPO_ROOT),
        ],
        check=True,
    )
    return tag


@pytest.fixture()
def fixture_workspace(tmp_path: Path) -> Path:
    """A fresh copy of the sample project, bind-mountable into the container.

    Each test gets its own copy so that one test's index state / settings
    don't bleed into another.
    """
    dst = tmp_path / "workspace"
    shutil.copytree(FIXTURE_PROJECT, dst)
    return dst


@pytest.fixture()
def container(
    docker_image: str,
    fixture_workspace: Path,
) -> Iterator[str]:
    """Start a fresh container with the sample project bind-mounted at /workspace.

    Uses an anonymous cocoindex-data volume so each test starts with a clean
    DB / model cache (the image's copy-up populates the cache from the
    baked-in path).
    """
    name = f"ccc-e2e-{uuid.uuid4().hex[:12]}"
    host_ws = str(fixture_workspace)
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
                "/var/cocoindex",  # anonymous volume per-test
                "-e",
                f"COCOINDEX_CODE_HOST_PATH_MAPPING=/workspace={host_ws}",
                docker_image,
            ],
            check=True,
            capture_output=True,
        )
        # Poll for the daemon socket so we know startup finished.
        _wait_for_daemon_ready(name, timeout=30.0)
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def _wait_for_daemon_ready(container_name: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_name,
                "sh",
                "-c",
                "test -S /var/run/cocoindex_code/daemon.sock",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise TimeoutError(f"Daemon in {container_name} did not become ready within {timeout}s")
