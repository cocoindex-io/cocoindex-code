"""Data models for CocoIndex Code."""

from dataclasses import dataclass


@dataclass
class QueryResult:
    """Result from a vector similarity query."""

    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    score: float
