"""Unit tests for shared CLI helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cocoindex_code import cli
from cocoindex_code.cli import (
    add_to_gitignore,
    remove_from_gitignore,
    require_project_root,
    resolve_default_path,
)


def test_require_project_root_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []")
    subdir = project / "src"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    # Create global settings so require_project_root doesn't reject
    settings_dir = tmp_path / "ccc_home"
    settings_dir.mkdir()
    (settings_dir / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))
    assert require_project_root() == project


def test_require_project_root_exits_when_not_initialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    monkeypatch.chdir(standalone)
    # Create global settings so we test the "no project" check, not "no global settings"
    settings_dir = tmp_path / "ccc_home"
    settings_dir.mkdir()
    (settings_dir / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))
    from click.exceptions import Exit

    with pytest.raises(Exit):
        require_project_root()


def test_require_project_root_auto_init_at_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With auto_init, a missing project is initialized at the enclosing git root."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    subdir = repo / "src"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    settings_dir = tmp_path / "ccc_home"
    settings_dir.mkdir()
    (settings_dir / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))

    assert require_project_root(auto_init=True) == repo
    assert (repo / ".cocoindex_code" / "settings.yml").is_file()
    assert "/.cocoindex_code/" in (repo / ".gitignore").read_text()
    assert "Created project settings" in capsys.readouterr().out


def test_require_project_root_auto_init_falls_back_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a parent git root, auto_init initializes the current directory."""
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    monkeypatch.chdir(standalone)
    settings_dir = tmp_path / "ccc_home"
    settings_dir.mkdir()
    (settings_dir / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))

    assert require_project_root(auto_init=True) == standalone
    assert (standalone / ".cocoindex_code" / "settings.yml").is_file()


def test_require_project_root_auto_init_global_settings_non_tty_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a TTY, auto_init never creates global settings — scripts must not
    silently commit to a default embedding model."""
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    monkeypatch.chdir(standalone)
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "no_ccc_home"))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    from click.exceptions import Exit

    with pytest.raises(Exit):
        require_project_root(auto_init=True)
    assert not (standalone / ".cocoindex_code").exists()


def test_require_project_root_auto_init_global_settings_tty_runs_init_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, auto_init runs the same interactive model setup as `ccc init`
    for missing global settings, then proceeds to initialize the project."""
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    monkeypatch.chdir(standalone)
    settings_dir = tmp_path / "ccc_home"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    def fake_setup(litellm_model_flag: str | None) -> None:
        settings_dir.mkdir()
        (settings_dir / "global_settings.yml").write_text(
            "embedding:\n  model: test\n  provider: litellm\n"
        )

    monkeypatch.setattr(cli, "_setup_user_settings_interactive", fake_setup)

    assert require_project_root(auto_init=True) == standalone
    assert (settings_dir / "global_settings.yml").is_file()
    assert (standalone / ".cocoindex_code" / "settings.yml").is_file()


def test_resolve_default_path_from_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    subdir = project_root / "src" / "lib"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    result = resolve_default_path(project_root)
    assert result == "src/lib/*"


def test_resolve_default_path_from_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = resolve_default_path(project_root)
    assert result is None


def test_resolve_default_path_outside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    result = resolve_default_path(project_root)
    assert result is None


# ---------------------------------------------------------------------------
# .gitignore helpers
# ---------------------------------------------------------------------------


def test_add_to_gitignore_creates_file(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    add_to_gitignore(tmp_path)
    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    content = gitignore.read_text()
    assert "# CocoIndex Code (ccc)" in content
    assert "/.cocoindex_code/" in content


def test_add_to_gitignore_appends_to_existing(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n")
    add_to_gitignore(tmp_path)
    content = gitignore.read_text()
    assert "*.pyc" in content
    assert "/.cocoindex_code/" in content


def test_add_to_gitignore_idempotent(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("/.cocoindex_code/\n")
    add_to_gitignore(tmp_path)
    content = gitignore.read_text()
    assert content.count("/.cocoindex_code/") == 1


def test_add_to_gitignore_skips_when_no_git(tmp_path: Path) -> None:
    add_to_gitignore(tmp_path)
    assert not (tmp_path / ".gitignore").exists()


def test_remove_from_gitignore(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n# CocoIndex Code (ccc)\n/.cocoindex_code/\n__pycache__/\n")
    remove_from_gitignore(tmp_path)
    content = gitignore.read_text()
    assert "/.cocoindex_code/" not in content
    assert "# CocoIndex Code (ccc)" not in content
    assert "*.pyc" in content
    assert "__pycache__/" in content


def test_remove_from_gitignore_no_entry(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    original = "*.pyc\n__pycache__/\n"
    gitignore.write_text(original)
    remove_from_gitignore(tmp_path)
    assert gitignore.read_text() == original


# ---------------------------------------------------------------------------
# COCOINDEX_CODE_HOST_CWD callback
# ---------------------------------------------------------------------------


def test_apply_host_cwd_chdirs_to_mapped_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When COCOINDEX_CODE_HOST_CWD is set and matches the mapping, chdir to container form."""
    from cocoindex_code.cli import _apply_host_cwd
    from cocoindex_code.settings import _reset_host_path_mapping_cache

    container = tmp_path / "workspace"
    host = tmp_path / "host-home"
    (container / "proj" / "src").mkdir(parents=True)
    host.mkdir()

    _reset_host_path_mapping_cache()
    monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", f"{container}={host}")
    monkeypatch.setenv("COCOINDEX_CODE_HOST_CWD", str(host / "proj" / "src"))

    _apply_host_cwd()

    # chdir resolves symlinks; compare resolved forms.
    assert Path.cwd().resolve() == (container / "proj" / "src").resolve()
    assert capsys.readouterr().err == ""

    _reset_host_path_mapping_cache()


def test_apply_host_cwd_warns_on_invalid_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An invalid COCOINDEX_CODE_HOST_CWD emits a warning but doesn't abort."""
    from cocoindex_code.cli import _apply_host_cwd

    original_cwd = Path.cwd()
    monkeypatch.setenv("COCOINDEX_CODE_HOST_CWD", "/nonexistent/path/xyz")
    monkeypatch.delenv("COCOINDEX_CODE_HOST_PATH_MAPPING", raising=False)

    _apply_host_cwd()

    captured = capsys.readouterr()
    assert "COCOINDEX_CODE_HOST_CWD" in captured.err
    assert "/nonexistent/path/xyz" in captured.err
    # cwd should be unchanged since chdir failed.
    assert Path.cwd() == original_cwd


def test_apply_host_cwd_noop_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With COCOINDEX_CODE_HOST_CWD unset, the callback is a silent no-op."""
    from cocoindex_code.cli import _apply_host_cwd

    original_cwd = Path.cwd()
    monkeypatch.delenv("COCOINDEX_CODE_HOST_CWD", raising=False)

    _apply_host_cwd()

    assert Path.cwd() == original_cwd
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# ccc init — auto-populate indexing_params / query_params from curated table
# ---------------------------------------------------------------------------


def test_init_auto_populates_known_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """For a known model, `ccc init` writes real indexing/query params into the
    file and prints an 'Applied recommended defaults' message.
    """
    from cocoindex_code.settings import EmbeddingSettings, load_user_settings

    user_dir = tmp_path / ".cocoindex_code"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_dir))

    monkeypatch.setattr(
        cli,
        "_resolve_embedding_choice",
        lambda **_kw: EmbeddingSettings(provider="litellm", model="cohere/embed-english-v3.0"),
    )
    monkeypatch.setattr(cli, "_run_init_model_check", lambda: True)

    cli._setup_user_settings_interactive(litellm_model_flag=None)

    loaded = load_user_settings()
    assert loaded.embedding.provider == "litellm"
    assert loaded.embedding.model == "cohere/embed-english-v3.0"
    assert loaded.embedding.indexing_params == {"input_type": "search_document"}
    assert loaded.embedding.query_params == {"input_type": "search_query"}

    out = capsys.readouterr().out
    assert "Applied recommended defaults" in out


def test_init_writes_comment_template_for_unknown_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For a model outside the curated table, `ccc init` writes a commented-out
    template block under ``embedding:`` instead of real keys.
    """
    from cocoindex_code.settings import (
        EmbeddingSettings,
        load_user_settings,
        user_settings_path,
    )

    user_dir = tmp_path / ".cocoindex_code"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_dir))

    monkeypatch.setattr(
        cli,
        "_resolve_embedding_choice",
        lambda **_kw: EmbeddingSettings(provider="litellm", model="someprovider/unknown-model"),
    )
    monkeypatch.setattr(cli, "_run_init_model_check", lambda: True)

    cli._setup_user_settings_interactive(litellm_model_flag=None)

    content = user_settings_path().read_text()
    # Commented template present, no populated keys
    assert "# indexing_params: {}" in content
    assert "# query_params: {}" in content
    loaded = load_user_settings()
    assert loaded.embedding.indexing_params is None
    assert loaded.embedding.query_params is None


def test_init_failed_check_prints_next_steps_and_keeps_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the model check fails and we can't re-prompt (non-interactive), the
    settings file is kept and a 'Next steps' recovery block is printed.
    """
    from cocoindex_code.settings import EmbeddingSettings, user_settings_path

    user_dir = tmp_path / ".cocoindex_code"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_dir))

    monkeypatch.setattr(
        cli,
        "_resolve_embedding_choice",
        lambda **_kw: EmbeddingSettings(provider="litellm", model="someprovider/unknown-model"),
    )
    monkeypatch.setattr(cli, "_run_init_model_check", lambda: False)
    # Non-interactive: no retry prompt, falls straight through to next steps.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    cli._setup_user_settings_interactive(litellm_model_flag=None)

    err = capsys.readouterr().err
    assert "Next steps" in err
    assert "ccc doctor" in err
    # Settings are kept on disk so the user can edit them.
    assert user_settings_path().is_file()


def test_resolve_embedding_choice_prefills_previous_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On retry, last attempt's provider and model become the prompt defaults."""
    import questionary

    from cocoindex_code.settings import EmbeddingSettings

    captured: dict[str, object] = {}

    class _FakeQuestion:
        def __init__(self, value: object) -> None:
            self._value = value

        def ask(self) -> object:
            return self._value

    def _fake_select(
        message: str, choices: object, default: object = None, **_kw: object
    ) -> _FakeQuestion:
        captured["select_default"] = default
        return _FakeQuestion("litellm")

    def _fake_text(message: str, default: str = "", **_kw: object) -> _FakeQuestion:
        captured["text_default"] = default
        return _FakeQuestion("openai/text-embedding-3-small")

    monkeypatch.setattr(questionary, "select", _fake_select)
    monkeypatch.setattr(questionary, "text", _fake_text)

    previous = EmbeddingSettings(provider="litellm", model="ollama/nomic-embed-text")
    result = cli._resolve_embedding_choice(
        litellm_model_flag=None,
        st_installed=True,
        tty=True,
        previous=previous,
    )

    # Provider select is pre-pointed at last time's provider; model is pre-filled.
    assert captured["select_default"] == "litellm"
    assert captured["text_default"] == "ollama/nomic-embed-text"
    assert result.provider == "litellm"
    assert result.model == "openai/text-embedding-3-small"


def test_st_model_rejection_reason_flags_ollama_prefix() -> None:
    """`ollama/` models can't be used with sentence-transformers; valid HF ids pass."""
    reason = cli._st_model_rejection_reason("ollama/nomic-embed-text")
    assert reason is not None and "litellm" in reason
    # Case-insensitive and whitespace-tolerant.
    assert cli._st_model_rejection_reason("  OLLAMA/foo ") is not None
    # Real HuggingFace ids with an `org/` slash must not false-positive.
    assert cli._st_model_rejection_reason("Snowflake/snowflake-arctic-embed-xs") is None
    assert cli._st_model_rejection_reason("openai/clip-vit-base-patch32") is None


def test_resolve_embedding_choice_validates_st_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sentence-transformers model prompt gets a validator that rejects
    `ollama/` models inline (before anything is written or tested)."""
    import questionary

    captured: dict[str, object] = {}

    class _FakeQuestion:
        def __init__(self, value: object) -> None:
            self._value = value

        def ask(self) -> object:
            return self._value

    def _fake_select(
        message: str, choices: object, default: object = None, **_kw: object
    ) -> _FakeQuestion:
        return _FakeQuestion("sentence-transformers")

    def _fake_text(
        message: str, default: str = "", validate: object = None, **_kw: object
    ) -> _FakeQuestion:
        captured["validate"] = validate
        return _FakeQuestion("Snowflake/snowflake-arctic-embed-xs")

    monkeypatch.setattr(questionary, "select", _fake_select)
    monkeypatch.setattr(questionary, "text", _fake_text)

    cli._resolve_embedding_choice(litellm_model_flag=None, st_installed=True, tty=True)

    validate = captured["validate"]
    assert callable(validate)
    assert validate("ollama/nomic-embed-text") is not True  # rejected (returns message)
    assert validate("Snowflake/snowflake-arctic-embed-xs") is True
