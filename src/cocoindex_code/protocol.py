"""IPC message types and serialization helpers for daemon communication."""

from __future__ import annotations

import msgspec as _msgspec

# ---------------------------------------------------------------------------
# Requests (tagged union via struct tag)
# ---------------------------------------------------------------------------


class HandshakeRequest(_msgspec.Struct, tag="handshake"):
    version: str


class IndexRequest(_msgspec.Struct, tag="index"):
    project_root: str


class SearchRequest(_msgspec.Struct, tag="search"):
    project_root: str
    query: str
    languages: list[str] | None = None
    paths: list[str] | None = None
    limit: int = 5
    offset: int = 0
    refresh: bool = False


class ProjectStatusRequest(_msgspec.Struct, tag="project_status"):
    project_root: str


class DaemonStatusRequest(_msgspec.Struct, tag="daemon_status"):
    pass


class RemoveProjectRequest(_msgspec.Struct, tag="remove_project"):
    project_root: str


class StopRequest(_msgspec.Struct, tag="stop"):
    pass


Request = (
    HandshakeRequest
    | IndexRequest
    | SearchRequest
    | ProjectStatusRequest
    | DaemonStatusRequest
    | RemoveProjectRequest
    | StopRequest
)

# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class HandshakeResponse(_msgspec.Struct, tag="handshake"):
    ok: bool
    daemon_version: str


class IndexResponse(_msgspec.Struct, tag="index"):
    success: bool
    message: str | None = None


class IndexingProgress(_msgspec.Struct):
    """Indexing stats snapshot, shared between progress updates and status responses."""

    num_execution_starts: int
    num_unchanged: int
    num_adds: int
    num_deletes: int
    num_reprocesses: int
    num_errors: int


class IndexProgressUpdate(_msgspec.Struct, tag="index_progress"):
    """Streamed during indexing — one per stats change, before the final IndexResponse."""

    progress: IndexingProgress


class IndexWaitingNotice(_msgspec.Struct, tag="index_waiting"):
    """Sent when another indexing is already in progress and the client must wait."""

    pass


class SearchResult(_msgspec.Struct):
    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    score: float


class SearchResponse(_msgspec.Struct, tag="search"):
    success: bool
    results: list[SearchResult] = []
    total_returned: int = 0
    offset: int = 0
    message: str | None = None


class ProjectStatusResponse(_msgspec.Struct, tag="project_status"):
    indexing: bool
    total_chunks: int
    total_files: int
    languages: dict[str, int]
    progress: IndexingProgress | None = None


class DaemonProjectInfo(_msgspec.Struct):
    project_root: str
    indexing: bool


class DaemonStatusResponse(_msgspec.Struct, tag="daemon_status"):
    version: str
    uptime_seconds: float
    projects: list[DaemonProjectInfo]


class RemoveProjectResponse(_msgspec.Struct, tag="remove_project"):
    ok: bool


class StopResponse(_msgspec.Struct, tag="stop"):
    ok: bool


class ErrorResponse(_msgspec.Struct, tag="error"):
    message: str


Response = (
    HandshakeResponse
    | IndexResponse
    | IndexProgressUpdate
    | IndexWaitingNotice
    | SearchResponse
    | ProjectStatusResponse
    | DaemonStatusResponse
    | RemoveProjectResponse
    | StopResponse
    | ErrorResponse
)

IndexStreamResponse = IndexProgressUpdate | IndexWaitingNotice | IndexResponse | ErrorResponse

# ---------------------------------------------------------------------------
# Encode / decode helpers (msgpack binary)
# ---------------------------------------------------------------------------

_request_encoder = _msgspec.msgpack.Encoder()
_request_decoder = _msgspec.msgpack.Decoder(Request)

_response_encoder = _msgspec.msgpack.Encoder()
_response_decoder = _msgspec.msgpack.Decoder(Response)


def encode_request(req: Request) -> bytes:
    return _request_encoder.encode(req)


def decode_request(data: bytes) -> Request:
    result: Request = _request_decoder.decode(data)
    return result


def encode_response(resp: Response) -> bytes:
    return _response_encoder.encode(resp)


def decode_response(data: bytes) -> Response:
    result: Response = _response_decoder.decode(data)
    return result
