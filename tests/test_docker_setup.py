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
    assert 'exec gosu coco "$@"' in content


def test_docker_compose_uses_sidecar_daemon_model() -> None:
    content = (REPO_ROOT / "docker" / "docker-compose.yml").read_text()

    assert "${COCOINDEX_CODE_IMAGE:-cocoindex-code:local-layered}" in content
    assert "${COCOINDEX_CODE_DAEMON_CONTAINER:-cocoindex-code-local-daemon}" in content
    assert ":/workspace" not in content
    assert "ports:" not in content
    assert (
        "${COCOINDEX_CODE_HOST_SETTINGS_DIR:-${HOME}/.cocoindex_code}"
        ":/home/coco/.cocoindex_code"
    ) in content
    assert "COCOINDEX_CODE_DAEMON_TCP: 0.0.0.0:8765" in content
    assert "COCOINDEX_CODE_DIR: /home/coco/.cocoindex_code" in content
    assert "COCOINDEX_CODE_STATE_DIR: /var/cocoindex/state" in content
    assert "COCOINDEX_CODE_RUNTIME_DIR: /var/run/cocoindex_code" in content
    assert "COCOINDEX_CODE_DB_PATH_MAPPING: /workspace=/var/cocoindex/db" in content
    assert "cocoindex-code-local-state:/var/cocoindex" in content
    assert "cocoindex-code-local-runtime:/var/run/cocoindex_code" in content
    assert "ccc daemon status" in content
    assert "daemon.sock" in content


def test_readme_documents_docker_state_runtime_and_host_cwd_mapping() -> None:
    content = (REPO_ROOT / "README.md").read_text()

    assert "central daemon + on-demand sidecar" in content
    assert "Sidecars mount only the authorized repo" in content
    assert "COCOINDEX_CODE_HOST_CWD=\"$PWD\"" in content
    assert "docker exec \"${flags[@]}\"" in content
    assert "ccc mcp" in content
    assert "COCOINDEX_CODE_STATE_DIR" in content
    assert "/var/cocoindex/state" in content
    assert "COCOINDEX_CODE_RUNTIME_DIR" in content
    assert "/var/run/cocoindex_code" in content
    assert "COCOINDEX_CODE_DB_PATH_MAPPING" in content
    assert "COCOINDEX_CODE_HOST_PATH_MAPPING" in content


def test_docker_sidecar_docs_describe_repo_scoped_architecture() -> None:
    content = (REPO_ROOT / "docs" / "docker-layered-indexing.md").read_text()

    assert "one central daemon container with no source-code mount" in content
    assert "short-lived sidecar containers" in content
    assert "Do not mount `$HOME` or a broad source tree" in content
    assert "COCOINDEX_CODE_DAEMON_TCP" in content
    assert "COCOINDEX_CODE_SIDECAR=1" in content


def test_sample_compose_uses_daemon_without_source_mount() -> None:
    content = (REPO_ROOT / "sample" / "docker-compose.yml").read_text()

    assert ":/workspace" not in content
    assert "ports:" not in content
    assert "COCOINDEX_CODE_DAEMON_TCP: 0.0.0.0:8765" in content
    assert (
        "${COCOINDEX_CODE_HOST_SETTINGS_DIR:-${HOME}/.cocoindex_code}"
        ":/home/coco/.cocoindex_code"
    ) in content
    assert "COCOINDEX_CODE_DIR: /home/coco/.cocoindex_code" in content
    assert "cocoindex-code-local-state:/var/cocoindex" in content
    assert "cocoindex-code-local-runtime:/var/run/cocoindex_code" in content


def test_sample_wrapper_mounts_only_authorized_repo_sidecar() -> None:
    content = (REPO_ROOT / "sample" / "bin" / "ccc").read_text()

    assert 'record_authorization "$root" "$common_dir"' in content
    assert '--volume "$root:$workspace_dir"' in content
    assert '--volume "$host_settings_dir:$container_settings_dir"' in content
    assert '--volume "$state_volume:$container_state_root"' in content
    assert '--volume "$runtime_volume:$container_runtime_dir"' in content
    assert '--network "$network"' in content
    assert "COCOINDEX_CODE_SIDECAR=1" in content
    assert "COCOINDEX_CODE_DAEMON_SUPERVISED=1" in content
    assert 'COCOINDEX_CODE_DAEMON_TCP=$daemon_connect_addr' in content
    assert "COCOINDEX_CODE_DIR=$container_settings_dir" in content
    assert "COCOINDEX_CODE_STATE_DIR=$container_state_dir" in content
    assert "COCOINDEX_CODE_RUNTIME_DIR=$container_runtime_dir" in content
    assert "COCOINDEX_CODE_DB_PATH_MAPPING=$container_db_path_mapping" in content
    assert 'COCOINDEX_CODE_HOST_PATH_MAPPING=$workspace_dir=$root' in content
    assert 'exec docker "${run_args[@]}"' in content


def test_sample_wrapper_defaults_settings_dir_to_host_home() -> None:
    content = (REPO_ROOT / "sample" / "bin" / "ccc").read_text()

    assert (
        'host_settings_dir="${COCOINDEX_CODE_HOST_SETTINGS_DIR:-$HOME/.cocoindex_code}"'
        in content
    )
    assert 'workspace_dir="${COCOINDEX_CODE_WORKSPACE_DIR:-/workspace}"' in content
    assert (
        'container_settings_dir="${COCOINDEX_CODE_CONTAINER_SETTINGS_DIR:-'
        '/home/coco/.cocoindex_code}"' in content
    )
    assert 'daemon_port="${COCOINDEX_CODE_DAEMON_PORT:-8765}"' in content
    assert (
        'daemon_connect_addr="${COCOINDEX_CODE_DAEMON_CONNECT:-'
        '$central_container:$daemon_port}"' in content
    )
    assert 'mkdir -p "$host_settings_dir"' in content


def test_sample_wrapper_authorization_handles_nested_repos_and_worktrees() -> None:
    content = (REPO_ROOT / "sample" / "bin" / "ccc").read_text()

    assert 'if (( ${#root} > ${#best} )); then' in content
    assert 'git_common_dir_for()' in content
    assert 'common_dir="$(git_common_dir_for "$root")"' in content
    assert 'if [[ "$common_dir" != "$root/.git" ]]; then' in content
    assert '--volume "$common_dir:$common_dir:ro"' in content


def test_sample_wrapper_refuses_unauthorized_paths_and_requires_git_for_init() -> None:
    content = (REPO_ROOT / "sample" / "bin" / "ccc").read_text()

    assert "ccc init must be run inside a Git repository for Docker authorization." in content
    assert "This path has not been authorized for Docker-backed ccc access:" in content
    assert "Run ccc init from the Git repo root or a subdirectory first." in content


def test_sample_gitignore_excludes_runtime_authorization_state() -> None:
    content = (REPO_ROOT / "sample" / ".gitignore").read_text()

    assert "data/" in content


def test_sample_makefile_has_default_image_and_reset_target() -> None:
    content = (REPO_ROOT / "sample" / "Makefile").read_text()

    assert "IMAGE ?= cocoindex-code:local-layered" in content
    assert "CCC_VARIANT ?= slim" in content
    assert "build: build-local" in content
    assert "build-local:" in content
    assert "--build-arg CCC_INSTALL_SPEC=/ccc-src" in content
    assert "build-pypi:" in content
    assert "reset: down" in content
    assert "docker volume rm" in content


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
    assert "cocoindex-code-daemon" in result.stdout
