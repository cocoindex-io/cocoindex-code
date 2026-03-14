"""Unit tests for the protocol module."""

from __future__ import annotations

from cocoindex_code.protocol import (
    DaemonProjectInfo,
    DaemonStatusRequest,
    DaemonStatusResponse,
    ErrorResponse,
    HandshakeRequest,
    IndexRequest,
    IndexResponse,
    ProjectStatusRequest,
    ProjectStatusResponse,
    Request,
    Response,
    SearchRequest,
    SearchResponse,
    SearchResult,
    StopRequest,
    StopResponse,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)


def test_encode_decode_handshake_request() -> None:
    req = HandshakeRequest(version="1.0.0")
    data = encode_request(req)
    decoded = decode_request(data)
    assert isinstance(decoded, HandshakeRequest)
    assert decoded.version == "1.0.0"


def test_encode_decode_search_request_with_defaults() -> None:
    req = SearchRequest(project_root="/tmp", query="test")
    data = encode_request(req)
    decoded = decode_request(data)
    assert isinstance(decoded, SearchRequest)
    assert decoded.languages is None
    assert decoded.limit == 5
    assert decoded.offset == 0
    assert decoded.refresh is False


def test_encode_decode_search_request_with_all_fields() -> None:
    req = SearchRequest(
        project_root="/tmp/proj",
        query="hello world",
        languages=["python", "rust"],
        paths=["src/*"],
        limit=20,
        offset=5,
        refresh=True,
    )
    data = encode_request(req)
    decoded = decode_request(data)
    assert isinstance(decoded, SearchRequest)
    assert decoded.project_root == "/tmp/proj"
    assert decoded.query == "hello world"
    assert decoded.languages == ["python", "rust"]
    assert decoded.paths == ["src/*"]
    assert decoded.limit == 20
    assert decoded.offset == 5
    assert decoded.refresh is True


def test_encode_decode_search_response_with_results() -> None:
    resp = SearchResponse(
        success=True,
        results=[
            SearchResult(
                file_path="main.py",
                language="python",
                content="def foo(): pass",
                start_line=1,
                end_line=1,
                score=0.95,
            ),
        ],
        total_returned=1,
        offset=0,
    )
    data = encode_response(resp)
    decoded = decode_response(data)
    assert isinstance(decoded, SearchResponse)
    assert decoded.success is True
    assert len(decoded.results) == 1
    assert decoded.results[0].file_path == "main.py"
    assert decoded.results[0].score == 0.95


def test_encode_decode_error_response() -> None:
    resp = ErrorResponse(message="something failed")
    data = encode_response(resp)
    decoded = decode_response(data)
    assert isinstance(decoded, ErrorResponse)
    assert decoded.message == "something failed"


def test_encode_decode_daemon_status_response() -> None:
    resp = DaemonStatusResponse(
        version="1.0.0",
        uptime_seconds=42.5,
        projects=[
            DaemonProjectInfo(project_root="/tmp/proj", indexing=False),
        ],
    )
    data = encode_response(resp)
    decoded = decode_response(data)
    assert isinstance(decoded, DaemonStatusResponse)
    assert decoded.version == "1.0.0"
    assert decoded.uptime_seconds == 42.5
    assert len(decoded.projects) == 1
    assert decoded.projects[0].project_root == "/tmp/proj"
    assert decoded.projects[0].indexing is False


def test_tagged_union_dispatch() -> None:
    req = IndexRequest(project_root="/tmp")
    data = encode_request(req)
    decoded = decode_request(data)
    assert isinstance(decoded, IndexRequest)
    assert not isinstance(decoded, HandshakeRequest)


def test_all_request_types_round_trip() -> None:
    requests: list[Request] = [
        HandshakeRequest(version="1.0.0"),
        IndexRequest(project_root="/tmp"),
        SearchRequest(project_root="/tmp", query="test"),
        ProjectStatusRequest(project_root="/tmp"),
        DaemonStatusRequest(),
        StopRequest(),
    ]
    for req in requests:
        data = encode_request(req)
        decoded = decode_request(data)
        assert type(decoded) is type(req)


def test_all_response_types_round_trip() -> None:
    responses: list[Response] = [
        IndexResponse(success=True),
        SearchResponse(success=True),
        ProjectStatusResponse(indexing=False, total_chunks=0, total_files=0, languages={}),
        DaemonStatusResponse(version="1.0.0", uptime_seconds=0.0, projects=[]),
        StopResponse(ok=True),
        ErrorResponse(message="err"),
    ]
    for resp in responses:
        data = encode_response(resp)
        decoded = decode_response(data)
        assert type(decoded) is type(resp)
