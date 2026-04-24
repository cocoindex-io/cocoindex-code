"""Unit tests for the settings module."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

# _resolve_chunker_registry is private to daemon.py (single call site), but its
# error paths (bad format, non-callable) are not exercised by integration tests.
from cocoindex_code.daemon import _resolve_chunker_registry
from cocoindex_code.settings import (
    DEFAULT_EXCLUDED_PATTERNS,
    DEFAULT_INCLUDED_PATTERNS,
    ChunkerMapping,
    EmbeddingSettings,
    LanguageOverride,
    ProjectSettings,
    UserSettings,
    _reset_db_path_mapping_cache,
    _reset_host_path_mapping_cache,
    default_project_settings,
    default_user_settings,
    find_parent_with_marker,
    find_project_root,
    format_path_for_display,
    get_host_path_mappings,
    load_project_settings,
    load_user_settings,
    normalize_input_path,
    resolve_db_dir,
    save_project_settings,
    save_user_settings,
)


@pytest.fixture()
def _patch_user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect user_settings_dir() to a temp directory."""
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_dir",
        lambda: tmp_path / ".cocoindex_code",
    )
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_path",
        lambda: tmp_path / ".cocoindex_code" / "global_settings.yml",
    )


def test_default_user_settings() -> None:
    s = default_user_settings()
    assert s.embedding.provider == "sentence-transformers"
    assert s.embedding.model == "Snowflake/snowflake-arctic-embed-xs"
    assert s.embedding.device is None
    assert s.embedding.min_interval_ms is None
    assert s.envs == {}


def test_default_project_settings() -> None:
    s = default_project_settings()
    assert s.include_patterns == DEFAULT_INCLUDED_PATTERNS
    assert s.exclude_patterns == DEFAULT_EXCLUDED_PATTERNS
    assert s.language_overrides == []


@pytest.mark.usefixtures("_patch_user_dir")
def test_save_and_load_user_settings(tmp_path: Path) -> None:
    settings = UserSettings(
        embedding=EmbeddingSettings(
            provider="litellm",
            model="gemini/text-embedding-004",
            device="cpu",
            min_interval_ms=300,
        ),
        envs={"GEMINI_API_KEY": "test-key"},
    )
    save_user_settings(settings)
    loaded = load_user_settings()
    assert loaded.embedding.provider == settings.embedding.provider
    assert loaded.embedding.model == settings.embedding.model
    assert loaded.embedding.device == settings.embedding.device
    assert loaded.embedding.min_interval_ms == settings.embedding.min_interval_ms
    assert loaded.envs == settings.envs


def test_save_and_load_project_settings(tmp_path: Path) -> None:
    settings = ProjectSettings(
        include_patterns=["**/*.py", "**/*.rs"],
        exclude_patterns=["**/target"],
        language_overrides=[LanguageOverride(ext="inc", lang="php")],
    )
    save_project_settings(tmp_path, settings)
    loaded = load_project_settings(tmp_path)
    assert loaded.include_patterns == settings.include_patterns
    assert loaded.exclude_patterns == settings.exclude_patterns
    assert len(loaded.language_overrides) == 1
    assert loaded.language_overrides[0].ext == "inc"
    assert loaded.language_overrides[0].lang == "php"


@pytest.mark.usefixtures("_patch_user_dir")
def test_load_user_settings_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_user_settings()


@pytest.mark.usefixtures("_patch_user_dir")
def test_load_user_settings_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / ".cocoindex_code" / "global_settings.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    with pytest.raises(ValueError):
        load_user_settings()


@pytest.mark.usefixtures("_patch_user_dir")
def test_load_user_settings_missing_model_raises(tmp_path: Path) -> None:
    path = tmp_path / ".cocoindex_code" / "global_settings.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("embedding:\n  provider: litellm\n")
    with pytest.raises(ValueError):
        load_user_settings()


@pytest.mark.usefixtures("_patch_user_dir")
def test_from_dict_missing_provider_defaults_to_litellm() -> None:
    from cocoindex_code.settings import _user_settings_from_dict

    settings = _user_settings_from_dict({"embedding": {"model": "some/model"}})
    assert settings.embedding.provider == "litellm"
    assert settings.embedding.model == "some/model"
    assert settings.embedding.min_interval_ms is None


@pytest.mark.usefixtures("_patch_user_dir")
def test_save_default_settings_writes_explicit_embedding() -> None:
    from cocoindex_code.settings import user_settings_path

    save_user_settings(default_user_settings())
    content = user_settings_path().read_text()
    assert "provider:" in content
    assert "model:" in content
    assert "Snowflake/snowflake-arctic-embed-xs" in content


def test_load_project_settings_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_project_settings(tmp_path)


def test_find_project_root_from_subdirectory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []")
    subdir = project / "src" / "lib"
    subdir.mkdir(parents=True)
    assert find_project_root(subdir) == project


def test_find_project_root_from_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []")
    assert find_project_root(project) == project


def test_find_project_root_returns_none_when_not_initialized(tmp_path: Path) -> None:
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    assert find_project_root(standalone) is None


def test_find_parent_with_marker_finds_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    subdir = repo / "src"
    subdir.mkdir()
    assert find_parent_with_marker(subdir) == repo


def test_find_parent_with_marker_prefers_cocoindex_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".cocoindex_code").mkdir(parents=True)
    subdir = repo / "src"
    subdir.mkdir()
    assert find_parent_with_marker(subdir) == repo


@pytest.mark.usefixtures("_patch_user_dir")
def test_user_settings_litellm_round_trip() -> None:
    settings = UserSettings(
        embedding=EmbeddingSettings(
            provider="litellm",
            model="gemini/text-embedding-004",
            min_interval_ms=250,
        ),
        envs={"GEMINI_API_KEY": "test"},
    )
    save_user_settings(settings)
    loaded = load_user_settings()
    assert loaded.embedding.provider == "litellm"
    assert loaded.embedding.model == "gemini/text-embedding-004"
    assert loaded.embedding.min_interval_ms == 250
    assert loaded.envs == {"GEMINI_API_KEY": "test"}


@pytest.mark.usefixtures("_patch_user_dir")
def test_load_user_settings_with_min_interval_ms(tmp_path: Path) -> None:
    path = tmp_path / ".cocoindex_code" / "global_settings.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "embedding:\n  provider: litellm\n  model: text-embedding-3-small\n  min_interval_ms: 300\n"
    )
    loaded = load_user_settings()
    assert loaded.embedding.provider == "litellm"
    assert loaded.embedding.model == "text-embedding-3-small"
    assert loaded.embedding.min_interval_ms == 300


def test_project_settings_with_language_overrides(tmp_path: Path) -> None:
    settings = ProjectSettings(
        language_overrides=[LanguageOverride(ext="inc", lang="php")],
    )
    save_project_settings(tmp_path, settings)
    loaded = load_project_settings(tmp_path)
    assert len(loaded.language_overrides) == 1
    assert loaded.language_overrides[0].ext == "inc"
    assert loaded.language_overrides[0].lang == "php"


class TestResolveDbDir:
    """Tests for COCOINDEX_CODE_DB_PATH_MAPPING and resolve_db_dir()."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        """Reset cached mapping before each test."""
        _reset_db_path_mapping_cache()
        monkeypatch.delenv("COCOINDEX_CODE_DB_PATH_MAPPING", raising=False)
        yield
        _reset_db_path_mapping_cache()

    def test_no_mapping(self, tmp_path: Path) -> None:
        project = tmp_path / "myproject"
        assert resolve_db_dir(project) == project / ".cocoindex_code"

    def test_single_mapping_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "workspace"
        dst = tmp_path / "db-files"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src}={dst}")
        assert resolve_db_dir(src / "myproject") == dst / "myproject"

    def test_exact_root_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "workspace"
        dst = tmp_path / "db-files"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src}={dst}")
        assert resolve_db_dir(src) == dst

    def test_no_match_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "workspace"
        dst = tmp_path / "db-files"
        other = tmp_path / "other" / "myproject"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src}={dst}")
        assert resolve_db_dir(other) == other / ".cocoindex_code"

    def test_multiple_mappings_first_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = tmp_path / "workspace"
        dst1 = tmp_path / "db1"
        dst2 = tmp_path / "db2"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src}={dst1},{src / 'sub'}={dst2}")
        assert resolve_db_dir(src / "sub" / "proj") == dst1 / "sub" / "proj"

    def test_multiple_mappings_second_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src1 = tmp_path / "workspace"
        src2 = tmp_path / "other"
        dst1 = tmp_path / "db1"
        dst2 = tmp_path / "db2"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src1}={dst1},{src2}={dst2}")
        assert resolve_db_dir(src2 / "proj") == dst2 / "proj"

    def test_no_partial_component_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = tmp_path / "workspace"
        dst = tmp_path / "db-files"
        other = tmp_path / "workspace2" / "proj"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src}={dst}")
        assert resolve_db_dir(other) == other / ".cocoindex_code"

    def test_rejects_relative_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", "relative/path=/db-files")
        with pytest.raises(ValueError, match="source path must be absolute"):
            resolve_db_dir(Path("/anything"))

    def test_rejects_relative_target(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "workspace"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src}=relative/path")
        with pytest.raises(ValueError, match="target path must be absolute"):
            resolve_db_dir(tmp_path / "anything")

    def test_skips_empty_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src1 = tmp_path / "workspace"
        src2 = tmp_path / "other"
        dst1 = tmp_path / "db-files"
        dst2 = tmp_path / "db2"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src1}={dst1},,{src2}={dst2},")
        assert resolve_db_dir(src2 / "proj") == dst2 / "proj"

    def test_nested_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "workspace"
        dst = tmp_path / "db-files"
        monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{src}={dst}")
        assert resolve_db_dir(src / "org" / "repo" / "subdir") == dst / "org" / "repo" / "subdir"


def test_project_settings_with_chunkers(tmp_path: Path) -> None:
    settings = ProjectSettings(
        chunkers=[ChunkerMapping(ext="toml", module="example_toml_chunker:toml_chunker")],
    )
    save_project_settings(tmp_path, settings)
    loaded = load_project_settings(tmp_path)
    assert len(loaded.chunkers) == 1
    assert loaded.chunkers[0].ext == "toml"
    assert loaded.chunkers[0].module == "example_toml_chunker:toml_chunker"


def test_resolve_chunker_registry_missing_colon() -> None:
    with pytest.raises(ValueError, match="module.path:callable"):
        _resolve_chunker_registry([ChunkerMapping(ext="toml", module="no_colon_here")])


def test_resolve_chunker_registry_not_callable() -> None:
    # os.path is a module attribute that is a string — not callable.
    with pytest.raises(ValueError, match="not callable"):
        _resolve_chunker_registry([ChunkerMapping(ext="toml", module="os:sep")])


@pytest.mark.usefixtures("_patch_user_dir")
def test_save_initial_user_settings_round_trip() -> None:
    from cocoindex_code.settings import (
        save_initial_user_settings,
        user_settings_path,
    )

    emb = EmbeddingSettings(
        provider="sentence-transformers",
        model="Snowflake/snowflake-arctic-embed-xs",
    )
    path = save_initial_user_settings(emb, defaults_applied=False)
    content = path.read_text()

    # Hint comment and the four commented env-var examples.
    assert "ccc doctor" in content
    assert "# envs:" in content
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "VOYAGE_API_KEY"):
        assert f"#   {key}:" in content

    # Must round-trip through the normal loader.
    loaded = load_user_settings()
    assert loaded.embedding.provider == "sentence-transformers"
    assert loaded.embedding.model == "Snowflake/snowflake-arctic-embed-xs"

    # user_settings_path() is the same path returned by save_initial_user_settings.
    assert path == user_settings_path()


@pytest.mark.usefixtures("_patch_user_dir")
def test_save_initial_user_settings_model_with_colon() -> None:
    """Regression: LiteLLM model names can contain `:`; must stay parseable."""
    from cocoindex_code.settings import save_initial_user_settings

    emb = EmbeddingSettings(
        provider="litellm",
        model="ollama_chat/llama3:latest",
    )
    save_initial_user_settings(emb, defaults_applied=False)

    loaded = load_user_settings()
    assert loaded.embedding.provider == "litellm"
    assert loaded.embedding.model == "ollama_chat/llama3:latest"


# ---------------------------------------------------------------------------
# Host path mapping (COCOINDEX_CODE_HOST_PATH_MAPPING)
# ---------------------------------------------------------------------------


class TestHostPathMapping:
    """Tests for format_path_for_display / normalize_input_path and the shared parser."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        _reset_host_path_mapping_cache()
        monkeypatch.delenv("COCOINDEX_CODE_HOST_PATH_MAPPING", raising=False)
        yield
        _reset_host_path_mapping_cache()

    def test_translates_display(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        container = tmp_path / "workspace"
        host = tmp_path / "alice"
        container.mkdir()
        host.mkdir()
        monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", f"{container}={host}")
        assert format_path_for_display(container / "proj" / "app.py") == str(
            host / "proj" / "app.py"
        )

    def test_translates_input(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        container = tmp_path / "workspace"
        host = tmp_path / "alice"
        container.mkdir()
        host.mkdir()
        monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", f"{container}={host}")
        assert normalize_input_path(host / "proj") == str(container / "proj")

    def test_unmatched_absolute_passes_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        container = tmp_path / "workspace"
        host = tmp_path / "alice"
        container.mkdir()
        host.mkdir()
        monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", f"{container}={host}")
        unrelated = "/etc/hosts"
        assert format_path_for_display(unrelated) == unrelated
        assert normalize_input_path(unrelated) == unrelated

    def test_relative_passes_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        container = tmp_path / "workspace"
        host = tmp_path / "alice"
        container.mkdir()
        host.mkdir()
        monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", f"{container}={host}")
        assert format_path_for_display("src/app.py") == "src/app.py"
        assert normalize_input_path("src/app.py") == "src/app.py"

    def test_first_match_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = tmp_path / "workspace"
        shared = ws / "shared"
        host_ws = tmp_path / "alice"
        host_shared = tmp_path / "mnt-shared"
        ws.mkdir()
        shared.mkdir(parents=True)
        host_ws.mkdir()
        host_shared.mkdir()
        monkeypatch.setenv(
            "COCOINDEX_CODE_HOST_PATH_MAPPING",
            f"{ws}={host_ws},{shared}={host_shared}",
        )
        # Path under shared — first mapping wins, not the more-specific one.
        assert format_path_for_display(shared / "docs" / "x") == str(
            host_ws / "shared" / "docs" / "x"
        )

    def test_env_unset_is_noop(self) -> None:
        # Fixture already clears env var.
        assert format_path_for_display("/workspace/x") == "/workspace/x"
        assert normalize_input_path("/workspace/x") == "/workspace/x"
        assert get_host_path_mappings() == []

    def test_invalid_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", "relative=/abs")
        with pytest.raises(ValueError, match="source path must be absolute"):
            get_host_path_mappings()


# ---------------------------------------------------------------------------
# find_parent_with_marker — global-only should not match
# ---------------------------------------------------------------------------


def test_find_parent_with_marker_skips_global_only(tmp_path: Path) -> None:
    """A workspace-root ``.cocoindex_code/`` holding only ``global_settings.yml``
    should NOT trigger the parent-marker check (it's not a project).
    """
    ws = tmp_path / "ws"
    (ws / ".cocoindex_code").mkdir(parents=True)
    (ws / ".cocoindex_code" / "global_settings.yml").write_text("embedding: {model: x}\n")
    subdir = ws / "myproject"
    subdir.mkdir()
    assert find_parent_with_marker(subdir) is None


def test_find_parent_with_marker_detects_project_settings(tmp_path: Path) -> None:
    """``.cocoindex_code/settings.yml`` at a parent is a real project marker."""
    repo = tmp_path / "repo"
    (repo / ".cocoindex_code").mkdir(parents=True)
    (repo / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []\n")
    subdir = repo / "src"
    subdir.mkdir()
    assert find_parent_with_marker(subdir) == repo


# ---------------------------------------------------------------------------
# daemon_runtime_dir
# ---------------------------------------------------------------------------


def test_daemon_runtime_dir_uses_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cocoindex_code._daemon_paths import daemon_runtime_dir

    target = tmp_path / "runtime"
    monkeypatch.setenv("COCOINDEX_CODE_RUNTIME_DIR", str(target))
    assert daemon_runtime_dir() == target


def test_daemon_runtime_dir_falls_back_to_user_settings_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When COCOINDEX_CODE_RUNTIME_DIR is unset, falls back to user_settings_dir()."""
    from cocoindex_code._daemon_paths import daemon_runtime_dir

    settings_dir = tmp_path / "settings"
    monkeypatch.delenv("COCOINDEX_CODE_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))
    assert daemon_runtime_dir() == settings_dir


# ---------------------------------------------------------------------------
# indexing_params / query_params round-trip and templates
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_user_dir")
def test_embedding_params_missing_load_as_none(tmp_path: Path) -> None:
    path = tmp_path / ".cocoindex_code" / "global_settings.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("embedding:\n  provider: litellm\n  model: m\n")
    loaded = load_user_settings()
    assert loaded.embedding.indexing_params is None
    assert loaded.embedding.query_params is None


@pytest.mark.usefixtures("_patch_user_dir")
def test_embedding_params_roundtrip_preserves_empty_dict() -> None:
    settings = UserSettings(
        embedding=EmbeddingSettings(
            provider="sentence-transformers",
            model="x/y",
            indexing_params={"prompt_name": "passage"},
            query_params={},  # explicit empty — must round-trip as {} not None
        ),
    )
    save_user_settings(settings)
    loaded = load_user_settings()
    assert loaded.embedding.indexing_params == {"prompt_name": "passage"}
    assert loaded.embedding.query_params == {}


@pytest.mark.usefixtures("_patch_user_dir")
def test_embedding_params_omit_when_none() -> None:
    settings = UserSettings(
        embedding=EmbeddingSettings(
            provider="litellm",
            model="m",
        ),
    )
    save_user_settings(settings)
    from cocoindex_code.settings import user_settings_path

    content = user_settings_path().read_text()
    assert "indexing_params" not in content
    assert "query_params" not in content


@pytest.mark.usefixtures("_patch_user_dir")
def test_save_initial_writes_populated_defaults_no_template() -> None:
    from cocoindex_code.settings import save_initial_user_settings, user_settings_path

    emb = EmbeddingSettings(
        provider="sentence-transformers",
        model="nomic-ai/CodeRankEmbed",
        query_params={"prompt_name": "query"},
    )
    save_initial_user_settings(emb, defaults_applied=True)
    content = user_settings_path().read_text()

    # Populated as real YAML keys
    assert "query_params:" in content
    assert "prompt_name: query" in content
    # No commented-out template hint
    assert "# indexing_params: {}" not in content
    assert "# query_params: {}" not in content


@pytest.mark.usefixtures("_patch_user_dir")
def test_save_initial_writes_comment_template_for_unknown_sentence_transformers() -> None:
    from cocoindex_code.settings import save_initial_user_settings, user_settings_path

    emb = EmbeddingSettings(
        provider="sentence-transformers",
        model="unknown/model",
    )
    save_initial_user_settings(emb, defaults_applied=False)
    content = user_settings_path().read_text()

    assert "# indexing_params: {}" in content
    assert "# query_params: {}" in content
    assert "prompt_name" in content
    # litellm-only keys should not appear
    assert "input_type" not in content


@pytest.mark.usefixtures("_patch_user_dir")
def test_save_initial_writes_comment_template_for_unknown_litellm() -> None:
    from cocoindex_code.settings import save_initial_user_settings, user_settings_path

    emb = EmbeddingSettings(
        provider="litellm",
        model="someprovider/unknown",
    )
    save_initial_user_settings(emb, defaults_applied=False)
    content = user_settings_path().read_text()

    assert "# indexing_params: {}" in content
    assert "# query_params: {}" in content
    assert "input_type" in content
    assert "dimensions" in content
