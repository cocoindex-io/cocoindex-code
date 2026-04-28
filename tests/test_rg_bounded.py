def test_import_rg_bounded():
    import cocoindex_code.rg_bounded as rg

    assert hasattr(rg, "run_bounded_rg")


def test_run_bounded_invalid_root(tmp_path):
    import cocoindex_code.rg_bounded as rg

    # Running against a non-existent path should raise ValueError via resolve_rg_paths
    res = rg.run_bounded_rg(tmp_path / "doesnotexist", "pattern")
    assert isinstance(res, dict)
    assert res.get("success") in (False,)
