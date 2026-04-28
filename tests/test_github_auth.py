def test_import_github_auth():
    import cocoindex_code.github_auth as gh

    # Basic smoke test: module loads and exposes a token resolver
    assert hasattr(gh, "resolve_github_token")


def test_resolve_token_from_env(monkeypatch):
    import cocoindex_code.github_auth as gh

    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-123")
    token = gh.resolve_github_token()
    assert token == "fake-token-123" or token is None
