"""Two configurations can index and search the same checkout independently."""

from pathlib import Path

import numpy as np
import pytest
from cocoindex.resources.schema import VectorSchema
from typer.testing import CliRunner

from cocoindex_code import cli, client, settings
from cocoindex_code._daemon_paths import daemon_socket_path
from cocoindex_code.project import Project


class _Embedder:
    def __coco_memo_key__(self) -> str:
        return "profile-test-embedder"

    async def __coco_vector_schema__(self) -> VectorSchema:
        return VectorSchema(dtype=np.dtype("float32"), size=4)

    async def embed(self, text: str, **kwargs: object) -> np.ndarray:
        return np.array([1, 0, 0, 0], dtype=np.float32)


async def test_profile_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "alpha.py").write_text("def alpha():\n    return 1\n")
    (repo / "beta.py").write_text("def beta():\n    return 2\n")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("COCOINDEX_CODE_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("COCOINDEX_CODE_DB_PATH_MAPPING", f"{repo}={tmp_path / 'db'}")
    settings._reset_db_path_mapping_cache()
    monkeypatch.setattr(client, "remove_project", lambda _root: None)
    monkeypatch.delenv(settings.PROFILE_ENV, raising=False)
    settings.save_project_settings(repo, settings.ProjectSettings())
    default_settings = settings.project_settings_path(repo)
    projects: dict[str, Project] = {}
    addresses = set()
    runner = CliRunner()
    try:
        for name in ("alpha", "beta"):
            monkeypatch.setenv(settings.PROFILE_ENV, name)
            assert settings.find_project_root(repo) is None
            user_settings = settings.UserSettings(settings.EmbeddingSettings(model=name))
            settings.save_user_settings(user_settings)
            initialized = runner.invoke(cli.app, ["init"])
            assert initialized.exit_code == 0, initialized.output
            settings.save_project_settings(
                repo, settings.ProjectSettings(include_patterns=[f"{name}.py"])
            )
            assert settings.resolve_db_dir(repo).is_relative_to(tmp_path / "db")
            addresses.add(daemon_socket_path())
            projects[name] = await Project.create(repo, _Embedder(), {}, {})
            await projects[name].run_index()

        assert len(addresses) == 2
        for name, project in projects.items():
            monkeypatch.setenv(settings.PROFILE_ENV, name)
            assert settings.load_user_settings().embedding.model == name
            assert [result.file_path for result in await project.search(name)] == [f"{name}.py"]
            project.close()

        reset = runner.invoke(cli.app, ["reset", "--all", "--force"])
        assert reset.exit_code == 0, reset.output
        assert not settings.target_sqlite_db_path(repo).exists()
        assert not settings.project_settings_path(repo).exists()
        assert default_settings.is_file()
        assert "/.cocoindex_code/" in (repo / ".gitignore").read_text()

        monkeypatch.setenv(settings.PROFILE_ENV, "alpha")
        assert settings.target_sqlite_db_path(repo).is_file()
        assert settings.project_settings_path(repo).is_file()
    finally:
        settings._reset_db_path_mapping_cache()


@pytest.mark.parametrize("name", ["../escape", "UPPERCASE", "foo/bar", "foo\\bar"])
def test_invalid_profile_is_rejected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(settings.PROFILE_ENV, name)
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 2
    assert "COCOINDEX_CODE_PROFILE" in result.output
