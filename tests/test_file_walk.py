"""Tests for shared source-file walking and gitignore filtering."""

from pathlib import Path

from cocoindex_code.file_walk import build_matcher, iter_included_files


def test_inverted_gitignore_keeps_source_directories_traversable(tmp_path: Path) -> None:
    """An ignore-all file can reopen directories and selected source extensions."""
    (tmp_path / ".gitignore").write_text(
        "\n".join(
            [
                "*",
                "!*/",
                "!*.cpp",
                "!*.h",
                "Content/",
                "",
            ]
        )
    )

    files = {
        "Root.cpp": "int root;\n",
        "Engine/Source/kept.cpp": "int kept;\n",
        "Engine/Source/kept.h": "#pragma once\n",
        "Engine/Source/ignored.bin": "generated\n",
        "Content/reignored.cpp": "int generated;\n",
    }
    for relative_path, contents in files.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    matcher = build_matcher(tmp_path, ["**/*.cpp", "**/*.h"], [])
    included = {
        relative_path.as_posix()
        for _, relative_path in iter_included_files(tmp_path, tmp_path, matcher)
    }

    assert included == {"Root.cpp", "Engine/Source/kept.cpp", "Engine/Source/kept.h"}
