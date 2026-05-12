from __future__ import annotations

from pathlib import Path, PurePath

from cocoindex_code.indexer import repo_key_for_path


def test_repo_key_for_path_uses_nested_git_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "ADK" / "a2a-samples"
    (repo / ".git").mkdir(parents=True)

    assert repo_key_for_path(PurePath("ADK/a2a-samples/src/main.py"), tmp_path) == (
        "ADK/a2a-samples"
    )


def test_repo_key_for_path_uses_root_git_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    assert repo_key_for_path(PurePath("src/main.py"), tmp_path) == "."


def test_repo_key_for_path_falls_back_to_top_level_component(tmp_path: Path) -> None:
    assert repo_key_for_path(PurePath("workspace/src/main.py"), tmp_path) == "workspace"
    assert repo_key_for_path(PurePath("README.md"), tmp_path) == "."
