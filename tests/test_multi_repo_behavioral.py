def test_link_local_repo(tmp_path):
    from cocoindex_code.config import CodebaseConfig, RepoConfig, RepoType
    from cocoindex_code.multi_repo import MultiRepoOrchestrator

    local_repo_dir = tmp_path / "local1"
    local_repo_dir.mkdir()
    (local_repo_dir / "file.txt").write_text("x")

    repo = RepoConfig(id="local1", type=RepoType.local, path=str(local_repo_dir))
    cfg = CodebaseConfig(repos=[repo])

    orchestrator = MultiRepoOrchestrator(
        cfg,
        config_path=tmp_path / "cfg.yml",
        unified_root=tmp_path / "unified",
        github_cache=tmp_path / "cache",
        repo_root_hint=tmp_path,
    )
    orchestrator.link_repos()
    link = (tmp_path / "unified") / "local1"
    assert link.is_symlink()
    assert link.resolve() == local_repo_dir.resolve()
