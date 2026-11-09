"""Exercise argv parsing through both user-facing CLI entry points."""

import os
import runpy
import sys
from importlib.metadata import distribution
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import typer.core

from cocoindex_code import client
from cocoindex_code.protocol import SearchResponse


@pytest.mark.parametrize("entrypoint", ["console", "module"])
@pytest.mark.parametrize("query", ["some query", "docs/*"])
def test_windows_search_preserves_globs(
    entrypoint: str, query: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matching local files must not replace search terms or path filters."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("first document", encoding="utf-8")
    (docs / "b.md").write_text("second document", encoding="utf-8")
    project_settings = tmp_path / ".cocoindex_code"
    project_settings.mkdir()
    (project_settings / "settings.yml").write_text("include_patterns: []", encoding="utf-8")
    user_settings = tmp_path / "ccc_home"
    user_settings.mkdir()
    (user_settings / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n", encoding="utf-8"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_settings))
    monkeypatch.delenv("COCOINDEX_CODE_HOST_CWD", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ccc", "search", query, "--path", "docs/*"])
    # Only select Typer's Windows parsing branch; leave pathlib's OS unchanged.
    monkeypatch.setattr(typer.core, "os", SimpleNamespace(**(vars(os) | {"name": "nt"})))
    search = Mock(return_value=SearchResponse(success=True))
    monkeypatch.setattr(client, "search", search)

    with pytest.raises(SystemExit) as exc_info:
        if entrypoint == "console":
            entry = next(
                entry
                for entry in distribution("cocoindex-code").entry_points
                if entry.group == "console_scripts" and entry.name == "ccc"
            )
            entry.load()()
        else:
            # Other tests import cli during collection; runpy needs a fresh module.
            monkeypatch.delitem(sys.modules, "cocoindex_code.cli", raising=False)
            runpy.run_module("cocoindex_code.cli", run_name="__main__", alter_sys=True)

    assert exc_info.value.code == 0
    search.assert_called_once()
    assert search.call_args.kwargs["query"] == query
    assert search.call_args.kwargs["paths"] == ["docs/*"]
