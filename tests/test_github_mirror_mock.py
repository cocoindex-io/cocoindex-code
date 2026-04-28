def test_github_mirror_sync_monkeypatch(tmp_path, monkeypatch):
    from cocoindex_code.github_mirror import GitHubMirror

    cache_root = tmp_path / "cache"

    mirror = GitHubMirror(
        owner_repo="octocat/Hello-World",
        branch="main",
        include_patterns=["*"],
        exclude_patterns=[],
        cache_root=cache_root,
        token=None,
    )

    def fake_http_json(self, url):
        # Simulate a GitHub tree API response with a single blob
        return (
            {
                "tree": [{"path": "README.md", "mode": "100644", "type": "blob", "sha": "abc123"}],
                "truncated": False,
                "sha": "deadbeef",
            },
            {"X-RateLimit-Remaining": "500", "X-RateLimit-Reset": "999999999"},
        )

    def fake_http_bytes(self, url):
        return b"content-bytes"

    monkeypatch.setattr(GitHubMirror, "_http_json", fake_http_json)
    monkeypatch.setattr(GitHubMirror, "_http_bytes", fake_http_bytes)

    result = mirror.sync(force=True)
    assert result.repo_id == mirror.repo_id
    assert result.fetched == 1
    assert result.skipped == 0
    assert result.removed == 0
    assert result.bytes_downloaded == len(b"content-bytes")
    assert result.success
