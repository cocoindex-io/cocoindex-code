from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_sets_container_native_state_defaults() -> None:
    content = (REPO_ROOT / "docker" / "Dockerfile").read_text()

    assert "git gosu" in content
    assert "COCOINDEX_CODE_STATE_DIR=/var/cocoindex/state" in content
    assert "COCOINDEX_CODE_RUNTIME_DIR=/var/run/cocoindex_code" in content
    assert "COCOINDEX_CODE_DB_PATH_MAPPING=/workspace=/var/cocoindex/db" in content
    assert "/var/cocoindex/state" in content


def test_docker_entrypoint_prepares_state_db_cache_and_runtime_dirs() -> None:
    content = (REPO_ROOT / "docker" / "entrypoint.sh").read_text()

    assert 'COCOINDEX_CODE_STATE_DIR=${COCOINDEX_CODE_STATE_DIR:-/var/cocoindex/state}' in content
    assert (
        'COCOINDEX_CODE_RUNTIME_DIR=${COCOINDEX_CODE_RUNTIME_DIR:-/var/run/cocoindex_code}'
        in content
    )
    assert '"$COCOINDEX_CODE_STATE_DIR"' in content
    assert "/var/cocoindex/db" in content
    assert '"$HF_HOME"' in content
    assert '"$SENTENCE_TRANSFORMERS_HOME"' in content
    assert '"$COCOINDEX_CODE_RUNTIME_DIR"' in content
    assert "chown -R coco:coco /var/cocoindex" in content


def test_docker_compose_exposes_local_use_knobs_and_healthcheck() -> None:
    content = (REPO_ROOT / "docker" / "docker-compose.yml").read_text()

    assert "${COCOINDEX_CODE_IMAGE:-cocoindex/cocoindex-code:latest}" in content
    assert "${COCOINDEX_CODE_CONTAINER_NAME:-cocoindex-code}" in content
    assert "${COCOINDEX_HOST_WORKSPACE:-${HOME}}:/workspace" in content
    assert "COCOINDEX_CODE_STATE_DIR: ${COCOINDEX_CODE_STATE_DIR:-/var/cocoindex/state}" in content
    assert (
        "COCOINDEX_CODE_RUNTIME_DIR: ${COCOINDEX_CODE_RUNTIME_DIR:-/var/run/cocoindex_code}"
        in content
    )
    assert (
        "COCOINDEX_CODE_DB_PATH_MAPPING: "
        "${COCOINDEX_CODE_DB_PATH_MAPPING:-/workspace=/var/cocoindex/db}"
    ) in content
    assert (
        "COCOINDEX_CODE_HOST_PATH_MAPPING: "
        "${COCOINDEX_CODE_HOST_PATH_MAPPING:-/workspace=${COCOINDEX_HOST_WORKSPACE:-${HOME}}}"
    ) in content
    assert "ccc daemon status" in content
    assert "daemon.sock" in content


def test_readme_documents_docker_state_runtime_and_host_cwd_mapping() -> None:
    content = (REPO_ROOT / "README.md").read_text()

    assert "COCOINDEX_CODE_HOST_CWD=\"$PWD\"" in content
    assert "docker exec \"${flags[@]}\"" in content
    assert "ccc mcp" in content
    assert "COCOINDEX_CODE_STATE_DIR" in content
    assert "/var/cocoindex/state" in content
    assert "COCOINDEX_CODE_RUNTIME_DIR" in content
    assert "/var/run/cocoindex_code" in content
    assert "COCOINDEX_CODE_DB_PATH_MAPPING" in content
    assert "COCOINDEX_CODE_HOST_PATH_MAPPING" in content


def test_docker_compose_config_is_valid(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI not available")

    compose_file = REPO_ROOT / "docker" / "docker-compose.yml"
    env = dict(os.environ)
    env.setdefault("HOME", str(tmp_path / "home"))
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "config"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0 and "docker daemon" in result.stderr.lower():
        pytest.skip("Docker daemon not available")
    assert result.returncode == 0, result.stderr
    assert "cocoindex-code" in result.stdout
