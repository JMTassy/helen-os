"""HELEN OS — Governed SQLite Operations.

SQL access under governance — read-only queries auto-ALLOW,
write operations require approval (WRITE_ACTION).

Safety:
    - db_query: SELECT only (rejects write statements at parse time)
    - db_execute: INSERT/UPDATE/DELETE/CREATE (requires approval)
    - Parameterized queries supported (prevents SQL injection)
    - Connection per-call (no shared mutable state)
    - In-memory default for testing; file-backed for production
"""
from __future__ import annotations

import re
import sqlite3
import time
from typing import Any, Dict, List

from helensh.tools import ToolResult


# ── Constants ───────────────────────────────────────────────────────

# Pattern detecting SQL write/DDL statements
_WRITE_SQL_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE)\b",
    re.IGNORECASE,
)

# Max rows returned by a single query
_MAX_ROWS = 10_000


# ── Helpers ─────────────────────────────────────────────────────────


def _get_connection(payload: dict) -> sqlite3.Connection:
    """Open a SQLite connection from payload config."""
    db_path = payload.get("db_path", ":memory:")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ── Executors ───────────────────────────────────────────────────────


def db_query(payload: dict, state: dict) -> ToolResult:
    """Execute a read-only SQL query.

    Payload:
        sql: str — SQL query (SELECT only)
        params: list — optional positional parameters for binding
        db_path: str — SQLite database path (default ":memory:")

    Returns ToolResult with query results:
        output.rows: list of dicts (column→value)
        output.row_count: number of rows returned
        output.has_more: whether more rows exist beyond MAX_ROWS
        output.columns: list of column names
    """
    start = time.monotonic()
    sql = payload.get("sql", "")
    params = payload.get("params", [])

    if not sql.strip():
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error="no SQL query specified", execution_ms=elapsed,
        )

    # Block write statements in read-only path
    if _WRITE_SQL_PATTERN.match(sql):
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error="db_query does not allow write operations; use db_execute",
            execution_ms=elapsed,
        )

    conn = None
    try:
        conn = _get_connection(payload)
        cursor = conn.execute(sql, params)
        rows = cursor.fetchmany(_MAX_ROWS)
        result = [dict(row) for row in rows]
        has_more = cursor.fetchone() is not None

        elapsed = (time.monotonic() - start) * 1000
        output: Dict[str, Any] = {
            "rows": result,
            "row_count": len(result),
            "has_more": has_more,
            "columns": [desc[0] for desc in cursor.description] if cursor.description else [],
        }

        db_path = payload.get("db_path", ":memory:")
        return ToolResult(
            success=True, output=output,
            artifacts=(db_path,) if db_path != ":memory:" else (),
            error=None, execution_ms=elapsed,
        )
    except sqlite3.Error as e:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"SQLite error: {e}", execution_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"{type(e).__name__}: {e}", execution_ms=elapsed,
        )
    finally:
        if conn is not None:
            conn.close()


def db_execute(payload: dict, state: dict) -> ToolResult:
    """Execute a write SQL statement.

    Payload:
        sql: str — SQL statement (INSERT/UPDATE/DELETE/CREATE/etc.)
        params: list — optional positional parameters for binding
        db_path: str — SQLite database path (default ":memory:")

    Returns ToolResult with:
        output.rows_affected: number of rows changed
        output.last_row_id: rowid of last inserted row
    """
    start = time.monotonic()
    sql = payload.get("sql", "")
    params = payload.get("params", [])

    if not sql.strip():
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error="no SQL statement specified", execution_ms=elapsed,
        )

    conn = None
    try:
        conn = _get_connection(payload)
        cursor = conn.execute(sql, params)
        conn.commit()

        elapsed = (time.monotonic() - start) * 1000
        output: Dict[str, Any] = {
            "rows_affected": cursor.rowcount,
            "last_row_id": cursor.lastrowid,
        }

        db_path = payload.get("db_path", ":memory:")
        return ToolResult(
            success=True, output=output,
            artifacts=(db_path,) if db_path != ":memory:" else (),
            error=None, execution_ms=elapsed,
        )
    except sqlite3.Error as e:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"SQLite error: {e}", execution_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            success=False, output=None, artifacts=(),
            error=f"{type(e).__name__}: {e}", execution_ms=elapsed,
        )
    finally:
        if conn is not None:
            conn.close()


__all__ = ["db_query", "db_execute"]
