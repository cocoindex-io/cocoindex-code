"""Helpers for registering ``ccc mcp`` with local coding-agent hosts."""

from __future__ import annotations

import json
import shutil
import subprocess
import os
from dataclasses import dataclass

_AUTO_HOST_ORDER = ("codex", "claude", "opencode", "generic")
_SUPPORTED_HOSTS = frozenset({"auto", "codex", "claude", "opencode", "generic"})


@dataclass(frozen=True)
class InstallPlan:
    """Resolved installation plan for one host."""

    host: str
    server_name: str
    command: list[str]
    detected: bool
    apply_supported: bool
    apply_command: list[str] | None = None
    snippet: str | None = None
    message: str | None = None
    next_steps: tuple[str, ...] = ()


def available_hosts() -> dict[str, bool]:
    """Return whether each supported host CLI is present on PATH."""
    return {
        "codex": shutil.which("codex") is not None,
        "claude": shutil.which("claude") is not None,
        "opencode": shutil.which("opencode") is not None,
        "generic": True,
    }


def detect_host(preferred: str = "auto") -> str:
    """Resolve a concrete host from ``preferred`` and local PATH state."""
    normalized = preferred.strip().lower()
    if normalized not in _SUPPORTED_HOSTS:
        supported = ", ".join(sorted(_SUPPORTED_HOSTS))
        raise ValueError(f"unsupported host {preferred!r}; expected one of: {supported}")
    if normalized != "auto":
        return normalized
    hosts = available_hosts()
    for host in _AUTO_HOST_ORDER:
        if hosts.get(host):
            return host
    return "generic"


def supported_hosts() -> tuple[str, ...]:
    """Return the list of supported host values accepted by the installer."""
    return tuple(sorted(_SUPPORTED_HOSTS))


def build_install_plan(host: str, *, server_name: str = "cocoindex-code") -> InstallPlan:
    """Build an installation plan for the requested host."""
    normalized = detect_host(host)
    detected = available_hosts().get(normalized, False)
    command = ["ccc", "mcp"]

    if normalized == "codex":
        return InstallPlan(
            host=normalized,
            server_name=server_name,
            command=command,
            detected=detected,
            apply_supported=True,
            apply_command=["codex", "mcp", "add", server_name, "--", *command],
            message="Register cocoindex-code with the local Codex MCP registry.",
            next_steps=(
                "Run `ccc install --apply --host codex` to register automatically.",
                "Inside a repo, use `cgrep \"your query\"` for local terminal search.",
                "In Codex, ask for `codebase_search`, `codebase_symbol`, "
                "or `codebase_workflow` when MCP context is needed.",
            ),
        )
    if normalized == "claude":
        return InstallPlan(
            host=normalized,
            server_name=server_name,
            command=command,
            detected=detected,
            apply_supported=True,
            apply_command=["claude", "mcp", "add", server_name, "--", *command],
            message="Register cocoindex-code with the local Claude Code MCP registry.",
            next_steps=(
                "Run `ccc install --apply --host claude` to register automatically.",
                "Inside a repo, use `cgrep \"your query\"` for local terminal search.",
                "In Claude Code, ask for `codebase_search`, `codebase_symbol`, "
                "or `codebase_workflow` when MCP context is needed.",
            ),
        )
    if normalized == "opencode":
        snippet = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                server_name: {
                    "type": "local",
                    "command": command,
                }
            },
        }
        return InstallPlan(
            host=normalized,
            server_name=server_name,
            command=command,
            detected=detected,
            apply_supported=False,
            snippet=json.dumps(snippet, indent=2),
            message="Add this block to opencode.json or use `opencode mcp add` interactively.",
            next_steps=(
                "Add the snippet to `opencode.json` or register the command interactively.",
                "Use `cgrep \"your query\"` locally when you want shell-native search outside MCP.",
            ),
        )

    snippet = {
        "mcpServers": {
            server_name: {
                "command": command[0],
                "args": command[1:],
            }
        }
    }
    return InstallPlan(
        host="generic",
        server_name=server_name,
        command=command,
        detected=True,
        apply_supported=False,
        snippet=json.dumps(snippet, indent=2),
        message="Use this generic MCP JSON snippet in hosts that accept inline MCP config.",
        next_steps=(
            "Drop the snippet into your host's MCP config.",
            "Use `cgrep \"your query\"` locally when you want shell-native search outside MCP.",
        ),
    )


def apply_install_plan(plan: InstallPlan) -> dict[str, object]:
    """Execute an installation plan when the host supports direct registration."""
    if not plan.apply_supported or plan.apply_command is None:
        return {
            "success": False,
            "host": plan.host,
            "error": f"{plan.host} installation cannot be applied automatically",
            "snippet": plan.snippet,
        }

    # Attempt to remove an existing MCP registration to make re-registration idempotent
    timeout_sec = int(os.environ.get("COCOINDEX_CLAUDE_MCP_TIMEOUT_SEC", "12"))
    try:
        # If the host supports a remove command, try removing first (ignore errors).
        if plan.host in ("claude", "codex"):
            remove_cmd = [plan.host, "mcp", "remove", plan.server_name]
            try:
                subprocess.run(remove_cmd, check=False, capture_output=True, text=True, timeout=timeout_sec)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                # best-effort; continue to add
                pass

        completed = subprocess.run(
            plan.apply_command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except FileNotFoundError as exc:
        return {
            "success": False,
            "host": plan.host,
            "error": str(exc),
            "command": plan.apply_command,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "host": plan.host,
            "error": f"timeout after {timeout_sec}s: {exc}",
            "command": plan.apply_command,
        }
    except subprocess.CalledProcessError as exc:
        return {
            "success": False,
            "host": plan.host,
            "error": exc.stderr.strip() or exc.stdout.strip() or str(exc),
            "command": plan.apply_command,
        }

    return {
        "success": True,
        "host": plan.host,
        "command": plan.apply_command,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
