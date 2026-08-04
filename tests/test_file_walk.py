"""Tests for shared source-file walking and gitignore filtering."""

from pathlib import Path

from cocoindex.resources.file import FilePathMatcher

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


def test_max_file_size_excludes_large_files(tmp_path: Path) -> None:
    """A size cap drops oversized files while leaving the rest of the walk intact."""
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "bundle.py").write_text("# padding\n" * 500)

    unlimited = build_matcher(tmp_path, ["**/*.py"], [])
    capped = build_matcher(tmp_path, ["**/*.py"], [], max_file_size=100)

    def walked(matcher: FilePathMatcher) -> set[str]:
        return {rel.as_posix() for _abs, rel in iter_included_files(tmp_path, tmp_path, matcher)}

    assert walked(unlimited) == {"small.py", "bundle.py"}
    assert walked(capped) == {"small.py"}


def test_max_file_size_keeps_files_at_the_limit(tmp_path: Path) -> None:
    """The cap is inclusive, so a file of exactly max_file_size bytes is kept."""
    (tmp_path / "exact.py").write_bytes(b"a" * 64)
    (tmp_path / "over.py").write_bytes(b"a" * 65)

    matcher = build_matcher(tmp_path, ["**/*.py"], [], max_file_size=64)
    walked = {rel.as_posix() for _abs, rel in iter_included_files(tmp_path, tmp_path, matcher)}

    assert walked == {"exact.py"}


def test_max_file_size_keeps_unstattable_files(tmp_path: Path) -> None:
    """A broken symlink cannot be sized, so the size cap leaves the decision alone."""
    (tmp_path / "broken.py").symlink_to(tmp_path / "missing.py")

    matcher = build_matcher(tmp_path, ["**/*.py"], [], max_file_size=1)
    walked = {rel.as_posix() for _abs, rel in iter_included_files(tmp_path, tmp_path, matcher)}

    assert walked == {"broken.py"}
