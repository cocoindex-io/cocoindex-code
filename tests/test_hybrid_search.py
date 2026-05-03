"""Tests for hybrid search (RRF) and exclude_paths functionality."""

from __future__ import annotations

import pytest

from cocoindex_code.protocol import SearchRequest, encode_request, decode_request
from cocoindex_code.query import _fuse_rrf, _RRF_K
from cocoindex_code.schema import QueryResult


# --- Protocol tests ---


def test_encode_decode_search_request_with_exclude_paths() -> None:
    req = SearchRequest(
        project_root="/tmp/test",
        query="auth",
        exclude_paths=["i18n/*", "*.min.js"],
    )
    data = encode_request(req)
    decoded = decode_request(data)
    assert isinstance(decoded, SearchRequest)
    assert decoded.exclude_paths == ["i18n/*", "*.min.js"]
    assert decoded.mode == "semantic"


def test_encode_decode_search_request_with_hybrid_mode() -> None:
    req = SearchRequest(
        project_root="/tmp/test",
        query="auth",
        mode="hybrid",
    )
    data = encode_request(req)
    decoded = decode_request(data)
    assert isinstance(decoded, SearchRequest)
    assert decoded.mode == "hybrid"


def test_encode_decode_search_request_backward_compat() -> None:
    """Existing requests without new fields should still decode correctly."""
    req = SearchRequest(
        project_root="/tmp/test",
        query="auth",
    )
    data = encode_request(req)
    decoded = decode_request(data)
    assert decoded.exclude_paths is None
    assert decoded.mode == "semantic"


# --- RRF fusion tests ---


def _make_result(path: str, line: int, score: float) -> QueryResult:
    return QueryResult(
        file_path=path,
        language="python",
        content=f"# code at {path}:{line}",
        start_line=line,
        end_line=line + 10,
        score=score,
    )


def test_fuse_rrf_vector_only() -> None:
    """When keyword results are empty, RRF returns vector results ranked."""
    vector = [
        _make_result("a.py", 1, 0.9),
        _make_result("b.py", 1, 0.8),
    ]
    result = _fuse_rrf(vector, [], limit=5)
    assert len(result) == 2
    assert result[0].file_path == "a.py"
    assert result[1].file_path == "b.py"


def test_fuse_rrf_keyword_only() -> None:
    """When vector results are empty, RRF returns keyword results."""
    keyword = [
        ("c.py", "python", "# code", 1, 10, 3),
        ("d.py", "python", "# code", 1, 10, 1),
    ]
    result = _fuse_rrf([], keyword, limit=5)
    assert len(result) == 2
    assert result[0].file_path == "c.py"


def test_fuse_rrf_consensus_boost() -> None:
    """Items in both lists should rank higher than items in only one."""
    # "a.py:1" appears in both lists
    vector = [
        _make_result("a.py", 1, 0.9),
        _make_result("b.py", 1, 0.8),
    ]
    keyword = [
        ("a.py", "python", "# code", 1, 10, 2),
        ("c.py", "python", "# code", 1, 10, 1),
    ]
    result = _fuse_rrf(vector, keyword, limit=5)
    # a.py:1 should be first (appears in both + consensus boost)
    assert result[0].file_path == "a.py"


def test_fuse_rrf_respects_limit() -> None:
    vector = [_make_result(f"f{i}.py", 1, 0.9 - i * 0.1) for i in range(10)]
    result = _fuse_rrf(vector, [], limit=3)
    assert len(result) == 3


def test_fuse_rrf_score_formula() -> None:
    """Verify RRF score follows 1/(k+rank) formula."""
    vector = [_make_result("a.py", 1, 0.9)]
    keyword = [("a.py", "python", "# code", 1, 10, 1)]
    result = _fuse_rrf(vector, keyword, limit=1)
    expected = 1.0 / (_RRF_K + 1) + 1.0 / (_RRF_K + 1) + 0.003  # both rank 1 + boost
    assert abs(result[0].score - expected) < 1e-6
