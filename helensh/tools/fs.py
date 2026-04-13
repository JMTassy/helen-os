"""HELEN OS — Governed Filesystem Operations.

Filesystem access under governance — sandboxed to a workspace directory.

Sandbox constraints:
    - All paths resolved relative to workspace
    - Path traversal (../) blocked via realpath containment check
    - Symlink-safe resolution
    - Read operations: fs_read, fs_list (no approval needed)
    - Write operations: fs_write (requires approval — WRITE_ACTION)

Workspace:
    Passed via payload["workspace"]. Defaults to current directory.
    In production, the CLI or session config sets this.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from helensh.tools import ToolResult


# ── Constants ───────────────────────────────────────────────────────

_DEFAULT_WORKSPACE = "."
_MAX_READ_SIZE = 10_000_000   # 10 MB
_MAX_WRITE_SIZE = 10_000_000  # 10 MB


# ── Path Safety ─────────────────────────────────────────────────────


def _resolve_safe(workspace: str, path: str) -> Optional[str]:
    """Resolve path safely within workspace boundary.

    Returns the resolved absolute path, or None if the path
    escapes the workspace (path traversal attempt).
    """
    workspace_real = os.path.realpath(os.path.abspath(workspace))
    target = os.path.realpath(os.path.join(workspace_real, path))
    # Containment check
    if target == workspace_real:
        return target
    if not target.startswith(workspace_real + os.sep):
        return None
    return target


# ── Executors ───────────────────────────────────────────────────────


def fs_read(payload: dict, state: dict) -> ToolResult:
    """Read a file from the governed workspace.

    Payload:
        path: str — relative path within workspace
        workspace: str — workspace root (default ".")

    Returns ToolResult with file contents as output.
    """
    start = time.monotonic()
    path = payload.get("path", "")
    workspace = payload.get("workspace", _DEFAULT_WORKSPACE)

    if not path:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error="no path specified", execution_ms=elapsed,
        )

    resolved = _resolve_safe(workspace, path)
    if resolved is None:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"path traversal blocked: '{path}' escapes workspace",
            execution_ms=elapsed,
        )

    try:
        size = os.path.getsize(resolved)
        if size > _MAX_READ_SIZE:
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(
                success=False, output=None, artifacts=(),
                error=f"file too large: {size} bytes (max {_MAX_READ_SIZE})",
                execution_ms=elapsed,
            )
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=True, output=content, artifacts=(resolved,),
            error=None, execution_ms=elapsed,
        )
    except FileNotFoundError:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"file not found: '{path}'", execution_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"{type(e).__name__}: {e}", execution_ms=elapsed,
        )


def fs_write(payload: dict, state: dict) -> ToolResult:
    """Write a file to the governed workspace.

    Payload:
        path: str — relative path within workspace
        content: str — content to write
        workspace: str — workspace root (default ".")

    Returns ToolResult with bytes written as output.
    """
    start = time.monotonic()
    path = payload.get("path", "")
    content = payload.get("content", "")
    workspace = payload.get("workspace", _DEFAULT_WORKSPACE)

    if not path:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error="no path specified", execution_ms=elapsed,
        )

    if isinstance(content, str) and len(content) > _MAX_WRITE_SIZE:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"content too large: {len(content)} bytes (max {_MAX_WRITE_SIZE})",
            execution_ms=elapsed,
        )

    resolved = _resolve_safe(workspace, path)
    if resolved is None:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"path traversal blocked: '{path}' escapes workspace",
            execution_ms=elapsed,
        )

    try:
        parent = os.path.dirname(resolved)
        os.makedirs(parent, exist_ok=True)

        with open(resolved, "w", encoding="utf-8") as f:
            chars_written = f.write(content)

        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=True, output=chars_written, artifacts=(resolved,),
            error=None, execution_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"{type(e).__name__}: {e}", execution_ms=elapsed,
        )


def fs_list(payload: dict, state: dict) -> ToolResult:
    """List directory contents in the governed workspace.

    Payload:
        path: str — relative path within workspace (default ".")
        workspace: str — workspace root (default ".")

    Returns ToolResult with sorted list of entries (dirs have trailing /).
    """
    start = time.monotonic()
    path = payload.get("path", ".")
    workspace = payload.get("workspace", _DEFAULT_WORKSPACE)

    resolved = _resolve_safe(workspace, path)
    if resolved is None:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"path traversal blocked: '{path}' escapes workspace",
            execution_ms=elapsed,
        )

    try:
        entries = sorted(os.listdir(resolved))
        annotated = []
        for entry in entries:
            full = os.path.join(resolved, entry)
            if os.path.isdir(full):
                annotated.append(entry + "/")
            else:
                annotated.append(entry)

        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=True, output=annotated, artifacts=(resolved,),
            error=None, execution_ms=elapsed,
        )
    except FileNotFoundError:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"directory not found: '{path}'", execution_ms=elapsed,
        )
    except NotADirectoryError:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"not a directory: '{path}'", execution_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"{type(e).__name__}: {e}", execution_ms=elapsed,
        )


__all__ = ["fs_read", "fs_write", "fs_list"]
