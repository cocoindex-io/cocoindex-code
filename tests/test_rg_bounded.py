def test_import_rg_bounded():
    import cocoindex_code.rg_bounded as rg

    assert hasattr(rg, "run_bounded_rg")


def test_run_bounded_invalid_root(tmp_path):
    import cocoindex_code.rg_bounded as rg

    # Running against a non-existent path should raise ValueError via resolve_rg_paths
    res = rg.run_bounded_rg(tmp_path / "doesnotexist", "pattern")
    assert isinstance(res, dict)
    assert res.get("success") in (False,)


def test_resolve_rg_paths_accepts_multiple_prefixes(tmp_path):
    import cocoindex_code.rg_bounded as rg

    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()

    cwd, args = rg.resolve_rg_paths(tmp_path, path_prefixes=["src", "scripts"])
    assert cwd == tmp_path.resolve()
    assert args == ["src", "scripts"]
