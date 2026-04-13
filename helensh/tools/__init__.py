"""HELEN OS — Governed Tool Registry.

Binds governed execution to real computational surfaces.
Every tool execution produces a ToolResult — an artifact
captured in the receipt but NOT in the receipt hash.

Architecture:
    proposal.action → ToolRegistry.get(action) → executor(payload, state) → ToolResult
    ToolResult → receipt.tool_result (observability, not consensus)

Provenance chain:
    Signal → Proposal → Validation → Stress → Execution → Artifact → Receipt
                                                            ↑
                                                    ToolResult (not in hash)

Hard constraint:
    ToolResult is an artifact. It does NOT influence receipt hash.
    ToolResult is NOT included in chain verification.
    ToolResult MUST NOT be mutated during replay.
    ToolResult captures what happened, never alters what was decided.

This is the same boundary as trace:
    - trace explains WHY a decision was made
    - tool_result records WHAT a tool produced
    Neither is in the receipt hash. Both are observability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── ToolResult ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolResult:
    """Immutable result of a governed tool execution.

    This is an artifact — it records what happened when a tool
    was executed, but it does not influence the governance decision.

    Fields:
        success: Whether the tool completed without error
        output: Primary output (stdout, query result, file content, return value)
        artifacts: Paths or identifiers of produced artifacts
        error: Error message if success is False
        execution_ms: Wall-clock execution time in milliseconds
    """
    success: bool
    output: Any
    artifacts: Tuple[str, ...]
    error: Optional[str]
    execution_ms: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict for receipt attachment."""
        return {
            "success": self.success,
            "output": self.output if _is_json_safe(self.output) else str(self.output),
            "artifacts": list(self.artifacts),
            "error": self.error,
            "execution_ms": self.execution_ms,
        }


def _is_json_safe(value: Any) -> bool:
    """Check if a value is JSON-serializable."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_safe(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_safe(v) for k, v in value.items())
    return False


# ── ToolRegistry ────────────────────────────────────────────────────


@dataclass
class _ToolEntry:
    """Internal registry entry."""
    name: str
    executor: Callable[[dict, dict], ToolResult]
    requires_approval: bool
    description: str


class ToolRegistry:
    """Central registry of governed tool executors.

    Maps action names to executor functions.
    Each executor: (payload: dict, state: dict) -> ToolResult

    Usage:
        registry = ToolRegistry()
        registry.register("python_exec", python_exec, requires_approval=True)
        result = registry.execute("python_exec", {"code": "2+2"}, state)
    """

    def __init__(self) -> None:
        self._tools: Dict[str, _ToolEntry] = {}

    def register(
        self,
        name: str,
        executor: Callable[[dict, dict], ToolResult],
        requires_approval: bool = True,
        description: str = "",
    ) -> None:
        """Register a tool executor for an action name."""
        self._tools[name] = _ToolEntry(
            name=name,
            executor=executor,
            requires_approval=requires_approval,
            description=description,
        )

    def has(self, name: str) -> bool:
        """Check if a tool is registered for the given action."""
        return name in self._tools

    def get(self, name: str) -> Optional[_ToolEntry]:
        """Get tool entry by name. Returns None if not registered."""
        return self._tools.get(name)

    def execute(self, name: str, payload: dict, state: dict) -> ToolResult:
        """Execute a registered tool.

        Returns ToolResult. If the tool is not registered, returns
        a failure ToolResult (never raises).
        """
        entry = self._tools.get(name)
        if entry is None:
            return ToolResult(
                success=False,
                output=None,
                artifacts=(),
                error=f"no tool registered for action '{name}'",
                execution_ms=0.0,
            )
        try:
            return entry.executor(payload, state)
        except Exception as e:
            return ToolResult(
                success=False,
                output=None,
                artifacts=(),
                error=f"{type(e).__name__}: {e}",
                execution_ms=0.0,
            )

    def list_tools(self) -> List[str]:
        """List all registered tool names, sorted."""
        return sorted(self._tools.keys())

    def tool_info(self) -> List[Dict[str, Any]]:
        """Return info dicts for all registered tools."""
        return [
            {
                "name": e.name,
                "requires_approval": e.requires_approval,
                "description": e.description,
            }
            for e in sorted(self._tools.values(), key=lambda e: e.name)
        ]


# ── Default Registry ────────────────────────────────────────────────


def default_registry() -> ToolRegistry:
    """Create a ToolRegistry with all built-in tool executors.

    Built-in tools:
        python_exec — Sandboxed Python execution (requires approval)
        fs_read     — Read a file from workspace
        fs_write    — Write a file to workspace (requires approval)
        fs_list     — List directory contents
        db_query    — SQL SELECT on SQLite (read-only)
        db_execute  — SQL write on SQLite (requires approval)
    """
    from helensh.tools.python_exec import python_exec
    from helensh.tools.fs import fs_read, fs_write, fs_list
    from helensh.tools.db import db_query, db_execute

    reg = ToolRegistry()
    reg.register("python_exec", python_exec, requires_approval=True,
                 description="Execute Python code in a sandboxed environment")
    reg.register("fs_read", fs_read, requires_approval=False,
                 description="Read a file from the governed workspace")
    reg.register("fs_write", fs_write, requires_approval=True,
                 description="Write a file to the governed workspace")
    reg.register("fs_list", fs_list, requires_approval=False,
                 description="List directory contents in the governed workspace")
    reg.register("db_query", db_query, requires_approval=False,
                 description="Execute a read-only SQL query on SQLite")
    reg.register("db_execute", db_execute, requires_approval=True,
                 description="Execute a write SQL statement on SQLite")

    return reg


# ── Exports ─────────────────────────────────────────────────────────

__all__ = [
    "ToolResult",
    "ToolRegistry",
    "default_registry",
]
