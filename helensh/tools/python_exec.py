"""HELEN OS — Sandboxed Python Execution.

Executes Python code in a restricted environment under governance.

Sandbox constraints:
    - No import statements (AST-checked)
    - No access to __import__, open, exec, eval, compile, breakpoint
    - No access to os, sys, subprocess, or any module
    - Stdout captured via StringIO
    - Timeout enforced via threading
    - Clean namespace per execution (no cross-execution leaks)

The last expression in the code is captured as the return value,
like a REPL: "2 + 2" -> output=4.
"""
from __future__ import annotations

import ast
import io
import threading
import time
from typing import Any, Dict

from helensh.tools import ToolResult


# ── Security ────────────────────────────────────────────────────────

# AST node types that are blocked
_BLOCKED_AST_NODES = (ast.Import, ast.ImportFrom)

# Safe builtins whitelist — no file I/O, no imports, no code generation
_SAFE_BUILTINS: Dict[str, Any] = {
    "abs": abs, "all": all, "any": any, "bin": bin, "bool": bool,
    "bytes": bytes, "callable": callable, "chr": chr, "complex": complex,
    "dict": dict, "divmod": divmod, "enumerate": enumerate,
    "filter": filter, "float": float, "format": format,
    "frozenset": frozenset, "getattr": getattr, "hasattr": hasattr,
    "hash": hash, "hex": hex, "id": id, "int": int,
    "isinstance": isinstance, "issubclass": issubclass, "iter": iter,
    "len": len, "list": list, "map": map, "max": max, "min": min,
    "next": next, "oct": oct, "ord": ord, "pow": pow,
    "range": range, "repr": repr, "reversed": reversed, "round": round,
    "set": set, "slice": slice, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "type": type, "zip": zip,
    "True": True, "False": False, "None": None,
}

# Maximum output size (bytes) to prevent memory exhaustion
_MAX_OUTPUT_SIZE = 1_000_000  # 1 MB

# Default and maximum timeout
_DEFAULT_TIMEOUT = 5.0
_MAX_TIMEOUT = 30.0


# ── Executor ────────────────────────────────────────────────────────


def python_exec(payload: dict, state: dict) -> ToolResult:
    """Execute Python code in a sandboxed environment.

    Payload:
        code: str — Python code to execute
        timeout: float — max execution time in seconds (default 5.0, max 30.0)

    Returns ToolResult with:
        output: return value of last expression, or captured stdout
        artifacts: empty (pure computation, no side effects)
        error: error message if execution failed
    """
    code = payload.get("code", "")
    timeout = min(payload.get("timeout", _DEFAULT_TIMEOUT), _MAX_TIMEOUT)

    if not code.strip():
        return ToolResult(
            success=True, output=None, artifacts=(),
            error=None, execution_ms=0.0,
        )

    start = time.monotonic()

    # ── 1. Parse to AST ──
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"SyntaxError: {e}", execution_ms=elapsed,
        )

    # ── 2. AST security check ──
    for node in ast.walk(tree):
        if isinstance(node, _BLOCKED_AST_NODES):
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(
                success=False, output=None, artifacts=(),
                error="import statements are not allowed in sandbox",
                execution_ms=elapsed,
            )

    # ── 3. Separate last expression for REPL-style return ──
    eval_code = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = tree.body.pop()
        eval_tree = ast.Expression(body=last_expr.value)
        ast.fix_missing_locations(eval_tree)
        eval_code = compile(eval_tree, "<sandbox>", "eval")

    exec_code = compile(tree, "<sandbox>", "exec")

    # ── 4. Build sandbox namespace ──
    stdout_capture = io.StringIO()
    sandbox_builtins = dict(_SAFE_BUILTINS)
    # Override print to capture to StringIO
    sandbox_builtins["print"] = lambda *args, **kwargs: print(
        *args,
        file=stdout_capture,
        **{k: v for k, v in kwargs.items() if k != "file"},
    )
    sandbox_globals: Dict[str, Any] = {"__builtins__": sandbox_builtins}

    # ── 5. Execute with timeout ──
    result_holder: list = [None]
    error_holder: list = [None]

    def _run() -> None:
        try:
            exec(exec_code, sandbox_globals)  # noqa: S102
            if eval_code is not None:
                result_holder[0] = eval(eval_code, sandbox_globals)  # noqa: S307
        except Exception as e:
            error_holder[0] = f"{type(e).__name__}: {e}"

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    elapsed = (time.monotonic() - start) * 1000

    if thread.is_alive():
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"execution timed out after {timeout}s",
            execution_ms=elapsed,
        )

    if error_holder[0] is not None:
        captured = stdout_capture.getvalue()
        return ToolResult(
            success=False,
            output=captured if captured else None,
            artifacts=(),
            error=error_holder[0],
            execution_ms=elapsed,
        )

    # ── 6. Collect output ──
    captured_stdout = stdout_capture.getvalue()

    # Prefer return value over stdout
    if result_holder[0] is not None:
        output = result_holder[0]
    elif captured_stdout:
        output = captured_stdout
    else:
        output = None

    # Truncate if too large
    if isinstance(output, str) and len(output) > _MAX_OUTPUT_SIZE:
        output = output[:_MAX_OUTPUT_SIZE] + "\n... [truncated]"

    return ToolResult(
        success=True, output=output, artifacts=(),
        error=None, execution_ms=elapsed,
    )


__all__ = ["python_exec"]
