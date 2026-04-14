"""Simple request handlers for the sample project."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .auth import verify_password


@dataclass
class Request:
    path: str
    body: dict[str, Any]


class RequestHandler:
    """Dispatch incoming requests to their handler methods."""

    def __init__(self, users: dict[str, tuple[str, str]]) -> None:
        self._users = users  # username -> (hashed_password, salt)

    def handle(self, req: Request) -> dict[str, Any]:
        if req.path == "/login":
            return self._login(req.body)
        return {"status": 404}

    def _login(self, body: dict[str, Any]) -> dict[str, Any]:
        username = body.get("username", "")
        password = body.get("password", "")
        entry = self._users.get(username)
        if entry is None:
            return {"status": 401}
        hashed, salt = entry
        if verify_password(password, hashed, salt):
            return {"status": 200, "user": username}
        return {"status": 401}
