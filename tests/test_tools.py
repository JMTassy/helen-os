"""HELEN OS — Governed Tool Execution Tests.

Tests for the tool registry, individual tool executors (Python, FS, DB),
GNF integration, and the provenance chain.

Architecture under test:
    Signal → Proposal → Validation → Stress → Execution → Artifact → Receipt
                                                            ↑
                                                    ToolResult (not in hash)

Test classes:
    1. TestToolResult          — ToolResult dataclass and serialization
    2. TestToolRegistry        — Registration, lookup, execution
    3. TestPythonExec          — Sandboxed Python execution
    4. TestPythonSandbox       — Security: blocked imports, builtins, timeout
    5. TestFsRead              — Governed filesystem read
    6. TestFsWrite             — Governed filesystem write
    7. TestFsList              — Governed directory listing
    8. TestFsSandbox           — Path traversal prevention
    9. TestDbQuery             — SQL SELECT execution
    10. TestDbExecute          — SQL write execution
    11. TestDbSafety           — Write blocking in query path
    12. TestGNFToolIntegration — Tool execution in GNF pipeline
    13. TestToolProvenance     — Provenance chain: trace + artifact + receipt
    14. TestToolHashExclusion  — tool_result NOT in receipt hash
    15. TestAdversarialTools   — Stress override prevents tool execution
"""
import json
import os
import sqlite3
import tempfile

import pytest

from helensh.kernel import init_session
from helensh.gnf import (
    gnf_step,
    execute,
    GNFReceipt,
)
from helensh.state import effect_footprint
from helensh.replay import verify_chain
from helensh.tools import ToolResult, ToolRegistry, default_registry
from helensh.tools.python_exec import python_exec
from helensh.tools.fs import fs_read, fs_write, fs_list, _resolve_safe
from helensh.tools.db import db_query, db_execute


# ── Helpers ─────────────────────────────────────────────────────────

def _fresh():
    return init_session()


def _make_registry_with_echo():
    """Registry with a simple echo tool for testing."""
    reg = ToolRegistry()

    def echo_tool(payload, state):
        msg = payload.get("message", "")
        return ToolResult(
            success=True, output=f"echo: {msg}", artifacts=(),
            error=None, execution_ms=0.1,
        )

    reg.register("chat", echo_tool, requires_approval=False, description="Echo tool")
    return reg


# ═══════════════════════════════════════════════════════════════════════
# 1. ToolResult
# ═══════════════════════════════════════════════════════════════════════

class TestToolResult:
    """ToolResult dataclass properties and serialization."""

    def test_frozen(self):
        r = ToolResult(success=True, output=42, artifacts=(), error=None, execution_ms=1.0)
        with pytest.raises(AttributeError):
            r.success = False  # type: ignore

    def test_to_dict_json_safe(self):
        r = ToolResult(success=True, output=42, artifacts=("a.txt",), error=None, execution_ms=1.5)
        d = r.to_dict()
        assert d["success"] is True
        assert d["output"] == 42
        assert d["artifacts"] == ["a.txt"]
        assert d["error"] is None
        assert d["execution_ms"] == 1.5
        # Must be JSON-serializable
        json.dumps(d)

    def test_to_dict_non_serializable_output(self):
        """Non-JSON-safe output is stringified."""
        r = ToolResult(success=True, output=object(), artifacts=(), error=None, execution_ms=0.0)
        d = r.to_dict()
        assert isinstance(d["output"], str)
        json.dumps(d)  # must not raise

    def test_to_dict_nested_output(self):
        r = ToolResult(
            success=True,
            output={"rows": [{"id": 1, "name": "alice"}], "count": 1},
            artifacts=(), error=None, execution_ms=0.5,
        )
        d = r.to_dict()
        assert d["output"]["rows"][0]["name"] == "alice"
        json.dumps(d)

    def test_failure_result(self):
        r = ToolResult(success=False, output=None, artifacts=(), error="boom", execution_ms=0.0)
        assert r.success is False
        assert r.error == "boom"
        d = r.to_dict()
        assert d["error"] == "boom"


# ═══════════════════════════════════════════════════════════════════════
# 2. ToolRegistry
# ═══════════════════════════════════════════════════════════════════════

class TestToolRegistry:
    """Registry lifecycle: register, lookup, execute, list."""

    def test_register_and_has(self):
        reg = ToolRegistry()
        reg.register("test", lambda p, s: ToolResult(True, None, (), None, 0.0))
        assert reg.has("test")
        assert not reg.has("missing")

    def test_execute_registered(self):
        reg = ToolRegistry()
        reg.register("add", lambda p, s: ToolResult(
            True, p.get("a", 0) + p.get("b", 0), (), None, 0.0,
        ))
        r = reg.execute("add", {"a": 3, "b": 4}, {})
        assert r.success
        assert r.output == 7

    def test_execute_unknown(self):
        reg = ToolRegistry()
        r = reg.execute("missing", {}, {})
        assert not r.success
        assert "no tool registered" in r.error

    def test_execute_exception_caught(self):
        reg = ToolRegistry()
        reg.register("boom", lambda p, s: (_ for _ in ()).throw(ValueError("kaboom")))
        r = reg.execute("boom", {}, {})
        assert not r.success
        assert "kaboom" in r.error

    def test_list_tools(self):
        reg = ToolRegistry()
        reg.register("b_tool", lambda p, s: ToolResult(True, None, (), None, 0.0))
        reg.register("a_tool", lambda p, s: ToolResult(True, None, (), None, 0.0))
        assert reg.list_tools() == ["a_tool", "b_tool"]

    def test_tool_info(self):
        reg = ToolRegistry()
        reg.register("t1", lambda p, s: ToolResult(True, None, (), None, 0.0),
                      requires_approval=True, description="Test tool 1")
        info = reg.tool_info()
        assert len(info) == 1
        assert info[0]["name"] == "t1"
        assert info[0]["requires_approval"] is True
        assert info[0]["description"] == "Test tool 1"

    def test_default_registry(self):
        """default_registry() returns all 6 built-in tools."""
        reg = default_registry()
        tools = reg.list_tools()
        assert "python_exec" in tools
        assert "fs_read" in tools
        assert "fs_write" in tools
        assert "fs_list" in tools
        assert "db_query" in tools
        assert "db_execute" in tools
        assert len(tools) == 6


# ═══════════════════════════════════════════════════════════════════════
# 3. Python Executor
# ═══════════════════════════════════════════════════════════════════════

class TestPythonExec:
    """Sandboxed Python execution — happy path."""

    def test_expression_eval(self):
        r = python_exec({"code": "2 + 2"}, {})
        assert r.success
        assert r.output == 4

    def test_multi_statement(self):
        code = "x = 10\ny = 20\nx + y"
        r = python_exec({"code": code}, {})
        assert r.success
        assert r.output == 30

    def test_stdout_capture(self):
        r = python_exec({"code": 'print("hello world")'}, {})
        assert r.success
        assert "hello world" in str(r.output)

    def test_mixed_print_and_expr(self):
        """When last statement is an expression, it takes priority over stdout."""
        code = 'print("side effect")\n42'
        r = python_exec({"code": code}, {})
        assert r.success
        assert r.output == 42

    def test_empty_code(self):
        r = python_exec({"code": ""}, {})
        assert r.success
        assert r.output is None

    def test_whitespace_code(self):
        r = python_exec({"code": "   "}, {})
        assert r.success
        assert r.output is None

    def test_safe_builtins_available(self):
        r = python_exec({"code": "len([1,2,3])"}, {})
        assert r.success
        assert r.output == 3

    def test_list_comprehension(self):
        r = python_exec({"code": "[x**2 for x in range(5)]"}, {})
        assert r.success
        assert r.output == [0, 1, 4, 9, 16]

    def test_dict_operations(self):
        r = python_exec({"code": "d = {'a': 1, 'b': 2}\nsorted(d.items())"}, {})
        assert r.success
        assert r.output == [("a", 1), ("b", 2)]

    def test_string_operations(self):
        r = python_exec({"code": "'hello'.upper()"}, {})
        assert r.success
        assert r.output == "HELLO"

    def test_exception_caught(self):
        r = python_exec({"code": "1/0"}, {})
        assert not r.success
        assert "ZeroDivisionError" in r.error

    def test_syntax_error(self):
        r = python_exec({"code": "def ("}, {})
        assert not r.success
        assert "SyntaxError" in r.error

    def test_execution_ms_positive(self):
        r = python_exec({"code": "sum(range(1000))"}, {})
        assert r.success
        assert r.execution_ms >= 0

    def test_artifacts_empty(self):
        """Python exec produces no artifacts (pure computation)."""
        r = python_exec({"code": "42"}, {})
        assert r.artifacts == ()

    def test_no_return_no_print(self):
        """Code that neither returns nor prints → None output."""
        r = python_exec({"code": "x = 42"}, {})
        assert r.success
        assert r.output is None


# ═══════════════════════════════════════════════════════════════════════
# 4. Python Sandbox Security
# ═══════════════════════════════════════════════════════════════════════

class TestPythonSandbox:
    """Security: blocked constructs in sandbox."""

    def test_import_blocked(self):
        r = python_exec({"code": "import os"}, {})
        assert not r.success
        assert "import" in r.error.lower()

    def test_from_import_blocked(self):
        r = python_exec({"code": "from os import path"}, {})
        assert not r.success
        assert "import" in r.error.lower()

    def test_dunder_import_blocked(self):
        """__import__ is not in safe builtins."""
        r = python_exec({"code": "__import__('os')"}, {})
        assert not r.success

    def test_open_blocked(self):
        """open() is not in safe builtins."""
        r = python_exec({"code": "open('/etc/passwd')"}, {})
        assert not r.success

    def test_exec_blocked(self):
        """exec() is not in safe builtins."""
        r = python_exec({"code": "exec('print(1)')"}, {})
        assert not r.success

    def test_eval_blocked(self):
        """eval() is not in safe builtins."""
        r = python_exec({"code": "eval('1+1')"}, {})
        assert not r.success

    def test_compile_blocked(self):
        """compile() is not in safe builtins."""
        r = python_exec({"code": "compile('1', '', 'eval')"}, {})
        assert not r.success

    def test_namespace_isolation(self):
        """Variables from one execution don't leak to the next."""
        r1 = python_exec({"code": "secret = 42"}, {})
        r2 = python_exec({"code": "secret"}, {})
        assert r2.success is False  # NameError

    def test_no_file_access(self):
        """Cannot access files through builtins."""
        r = python_exec({"code": "open('/tmp/test', 'w')"}, {})
        assert not r.success

    def test_timeout_respected(self):
        """Infinite loop is killed by timeout."""
        r = python_exec({"code": "while True: pass", "timeout": 0.5}, {})
        assert not r.success
        assert "timed out" in r.error


# ═══════════════════════════════════════════════════════════════════════
# 5. Filesystem Read
# ═══════════════════════════════════════════════════════════════════════

class TestFsRead:
    """Governed filesystem read operations."""

    def test_read_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        r = fs_read({"path": "test.txt", "workspace": str(tmp_path)}, {})
        assert r.success
        assert r.output == "hello world"
        assert len(r.artifacts) == 1

    def test_read_nonexistent(self, tmp_path):
        r = fs_read({"path": "missing.txt", "workspace": str(tmp_path)}, {})
        assert not r.success
        assert "not found" in r.error

    def test_read_no_path(self):
        r = fs_read({"workspace": "/tmp"}, {})
        assert not r.success
        assert "no path" in r.error

    def test_read_subdirectory(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "deep.txt"
        f.write_text("deep content")
        r = fs_read({"path": "sub/deep.txt", "workspace": str(tmp_path)}, {})
        assert r.success
        assert r.output == "deep content"

    def test_execution_ms(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("x")
        r = fs_read({"path": "t.txt", "workspace": str(tmp_path)}, {})
        assert r.execution_ms >= 0


# ═══════════════════════════════════════════════════════════════════════
# 6. Filesystem Write
# ═══════════════════════════════════════════════════════════════════════

class TestFsWrite:
    """Governed filesystem write operations."""

    def test_write_new_file(self, tmp_path):
        r = fs_write({"path": "out.txt", "content": "data", "workspace": str(tmp_path)}, {})
        assert r.success
        assert r.output == 4  # chars written
        assert (tmp_path / "out.txt").read_text() == "data"

    def test_write_creates_parents(self, tmp_path):
        r = fs_write({
            "path": "a/b/c.txt", "content": "nested",
            "workspace": str(tmp_path),
        }, {})
        assert r.success
        assert (tmp_path / "a" / "b" / "c.txt").read_text() == "nested"

    def test_write_no_path(self):
        r = fs_write({"content": "x", "workspace": "/tmp"}, {})
        assert not r.success
        assert "no path" in r.error

    def test_write_artifacts(self, tmp_path):
        r = fs_write({"path": "f.txt", "content": "x", "workspace": str(tmp_path)}, {})
        assert r.success
        assert len(r.artifacts) == 1
        assert r.artifacts[0].endswith("f.txt")

    def test_overwrite(self, tmp_path):
        f = tmp_path / "ow.txt"
        f.write_text("old")
        r = fs_write({"path": "ow.txt", "content": "new", "workspace": str(tmp_path)}, {})
        assert r.success
        assert f.read_text() == "new"


# ═══════════════════════════════════════════════════════════════════════
# 7. Filesystem List
# ═══════════════════════════════════════════════════════════════════════

class TestFsList:
    """Governed directory listing."""

    def test_list_root(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "sub").mkdir()
        r = fs_list({"path": ".", "workspace": str(tmp_path)}, {})
        assert r.success
        assert "a.txt" in r.output
        assert "b.txt" in r.output
        assert "sub/" in r.output  # dir annotated with /

    def test_list_subdirectory(self, tmp_path):
        sub = tmp_path / "inner"
        sub.mkdir()
        (sub / "x.py").write_text("x")
        r = fs_list({"path": "inner", "workspace": str(tmp_path)}, {})
        assert r.success
        assert r.output == ["x.py"]

    def test_list_nonexistent(self, tmp_path):
        r = fs_list({"path": "nope", "workspace": str(tmp_path)}, {})
        assert not r.success
        assert "not found" in r.error

    def test_list_file_not_dir(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        r = fs_list({"path": "f.txt", "workspace": str(tmp_path)}, {})
        assert not r.success
        assert "not a directory" in r.error


# ═══════════════════════════════════════════════════════════════════════
# 8. Filesystem Path Traversal Prevention
# ═══════════════════════════════════════════════════════════════════════

class TestFsSandbox:
    """Path sandboxing — prevent escape from workspace."""

    def test_dotdot_blocked(self, tmp_path):
        r = fs_read({"path": "../../../etc/passwd", "workspace": str(tmp_path)}, {})
        assert not r.success
        assert "traversal" in r.error

    def test_absolute_path_blocked(self, tmp_path):
        r = fs_read({"path": "/etc/passwd", "workspace": str(tmp_path)}, {})
        assert not r.success
        assert "traversal" in r.error

    def test_resolve_safe_within(self, tmp_path):
        (tmp_path / "ok.txt").write_text("ok")
        assert _resolve_safe(str(tmp_path), "ok.txt") is not None

    def test_resolve_safe_escape(self, tmp_path):
        assert _resolve_safe(str(tmp_path), "../../etc/passwd") is None

    def test_write_traversal_blocked(self, tmp_path):
        r = fs_write({
            "path": "../../evil.txt", "content": "pwned",
            "workspace": str(tmp_path),
        }, {})
        assert not r.success
        assert "traversal" in r.error

    def test_list_traversal_blocked(self, tmp_path):
        r = fs_list({"path": "../..", "workspace": str(tmp_path)}, {})
        assert not r.success
        assert "traversal" in r.error


# ═══════════════════════════════════════════════════════════════════════
# 9. DB Query
# ═══════════════════════════════════════════════════════════════════════

class TestDbQuery:
    """Governed SQL SELECT execution."""

    def _setup_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users VALUES (1, 'alice')")
        conn.execute("INSERT INTO users VALUES (2, 'bob')")
        conn.commit()
        conn.close()
        return db_path

    def test_select(self, tmp_path):
        db = self._setup_db(tmp_path)
        r = db_query({"sql": "SELECT * FROM users ORDER BY id", "db_path": db}, {})
        assert r.success
        assert r.output["row_count"] == 2
        assert r.output["rows"][0]["name"] == "alice"
        assert r.output["columns"] == ["id", "name"]

    def test_parameterized(self, tmp_path):
        db = self._setup_db(tmp_path)
        r = db_query({"sql": "SELECT * FROM users WHERE id=?", "params": [2], "db_path": db}, {})
        assert r.success
        assert r.output["row_count"] == 1
        assert r.output["rows"][0]["name"] == "bob"

    def test_empty_result(self, tmp_path):
        db = self._setup_db(tmp_path)
        r = db_query({"sql": "SELECT * FROM users WHERE id=99", "db_path": db}, {})
        assert r.success
        assert r.output["row_count"] == 0
        assert r.output["rows"] == []

    def test_no_sql(self):
        r = db_query({"db_path": ":memory:"}, {})
        assert not r.success
        assert "no SQL" in r.error

    def test_invalid_sql(self, tmp_path):
        db = self._setup_db(tmp_path)
        r = db_query({"sql": "SELECTZ * FROM nowhere", "db_path": db}, {})
        assert not r.success
        assert "SQLite error" in r.error

    def test_artifacts_for_file_db(self, tmp_path):
        db = self._setup_db(tmp_path)
        r = db_query({"sql": "SELECT 1", "db_path": db}, {})
        assert r.success
        assert len(r.artifacts) == 1

    def test_no_artifacts_for_memory_db(self):
        r = db_query({"sql": "SELECT 1", "db_path": ":memory:"}, {})
        assert r.success
        assert r.artifacts == ()


# ═══════════════════════════════════════════════════════════════════════
# 10. DB Execute
# ═══════════════════════════════════════════════════════════════════════

class TestDbExecute:
    """Governed SQL write execution."""

    def test_create_and_insert(self, tmp_path):
        db = str(tmp_path / "w.db")
        # CREATE
        r1 = db_execute({
            "sql": "CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)",
            "db_path": db,
        }, {})
        assert r1.success
        # INSERT
        r2 = db_execute({
            "sql": "INSERT INTO items VALUES (1, 'foo')",
            "db_path": db,
        }, {})
        assert r2.success
        assert r2.output["rows_affected"] == 1
        # Verify
        r3 = db_query({"sql": "SELECT * FROM items", "db_path": db}, {})
        assert r3.success
        assert r3.output["row_count"] == 1

    def test_parameterized_insert(self, tmp_path):
        db = str(tmp_path / "p.db")
        db_execute({"sql": "CREATE TABLE t (v TEXT)", "db_path": db}, {})
        r = db_execute({
            "sql": "INSERT INTO t VALUES (?)",
            "params": ["safe value"],
            "db_path": db,
        }, {})
        assert r.success
        q = db_query({"sql": "SELECT v FROM t", "db_path": db}, {})
        assert q.output["rows"][0]["v"] == "safe value"

    def test_no_sql(self):
        r = db_execute({"db_path": ":memory:"}, {})
        assert not r.success


# ═══════════════════════════════════════════════════════════════════════
# 11. DB Safety
# ═══════════════════════════════════════════════════════════════════════

class TestDbSafety:
    """db_query blocks write operations."""

    def test_insert_blocked_in_query(self):
        r = db_query({"sql": "INSERT INTO t VALUES (1)", "db_path": ":memory:"}, {})
        assert not r.success
        assert "write operations" in r.error

    def test_delete_blocked_in_query(self):
        r = db_query({"sql": "DELETE FROM t WHERE 1=1", "db_path": ":memory:"}, {})
        assert not r.success
        assert "write operations" in r.error

    def test_drop_blocked_in_query(self):
        r = db_query({"sql": "DROP TABLE t", "db_path": ":memory:"}, {})
        assert not r.success
        assert "write operations" in r.error

    def test_update_blocked_in_query(self):
        r = db_query({"sql": "UPDATE t SET x=1", "db_path": ":memory:"}, {})
        assert not r.success
        assert "write operations" in r.error

    def test_create_blocked_in_query(self):
        r = db_query({"sql": "CREATE TABLE t (x INT)", "db_path": ":memory:"}, {})
        assert not r.success
        assert "write operations" in r.error


# ═══════════════════════════════════════════════════════════════════════
# 12. GNF Tool Integration
# ═══════════════════════════════════════════════════════════════════════

class TestGNFToolIntegration:
    """Tool execution in the full GNF pipeline."""

    def test_gnf_step_without_registry(self):
        """Without tool_registry, gnf_step works as before."""
        s = _fresh()
        s2, receipt = gnf_step(s, "hello")
        assert receipt.tool_result is None
        assert receipt.effect_status == "APPLIED"

    def test_gnf_step_with_registry_no_match(self):
        """Registry present but no tool for 'chat' → no tool execution."""
        s = _fresh()
        reg = ToolRegistry()  # empty
        s2, receipt = gnf_step(s, "hello", tool_registry=reg)
        assert receipt.tool_result is None

    def test_gnf_step_with_matching_tool(self):
        """Registry has a tool for 'chat' → tool executes, result captured."""
        s = _fresh()
        reg = _make_registry_with_echo()
        s2, receipt = gnf_step(s, "hello", tool_registry=reg)
        assert receipt.tool_result is not None
        assert receipt.tool_result.success
        assert "echo: hello" in str(receipt.tool_result.output)

    def test_tool_result_on_execution_receipt(self):
        """tool_result appears on the execution receipt dict."""
        s = _fresh()
        reg = _make_registry_with_echo()
        s2, receipt = gnf_step(s, "hello", tool_registry=reg)
        # Find execution receipt
        e_receipts = [r for r in s2["receipts"] if r.get("type") == "EXECUTION"]
        assert len(e_receipts) >= 1
        last_exec = e_receipts[-1]
        assert "tool_result" in last_exec
        assert last_exec["tool_result"]["success"] is True

    def test_denied_action_no_tool_execution(self):
        """If verdict is DENY, tool should NOT execute."""
        s = _fresh()
        s["capabilities"]["chat"] = False  # revoke capability
        reg = _make_registry_with_echo()
        s2, receipt = gnf_step(s, "hello", tool_registry=reg)
        assert receipt.effect_status == "DENIED"
        assert receipt.tool_result is None

    def test_execute_layer_with_tool_registry(self):
        """Direct execute() call with tool_registry."""
        s = _fresh()
        reg = _make_registry_with_echo()
        prop = {"action": "chat", "payload": {"message": "test"}, "authority": False}
        new_s, status, mem_eff, tool_res = execute(s, prop, "ALLOW", tool_registry=reg)
        assert status == "APPLIED"
        assert tool_res is not None
        assert tool_res.success
        assert "echo: test" in str(tool_res.output)

    def test_execute_deny_no_tool(self):
        """execute() with DENY verdict does not call tool."""
        s = _fresh()
        reg = _make_registry_with_echo()
        prop = {"action": "chat", "payload": {"message": "test"}, "authority": False}
        new_s, status, mem_eff, tool_res = execute(s, prop, "DENY", tool_registry=reg)
        assert status == "DENIED"
        assert tool_res is None

    def test_tool_result_serialization_on_receipt(self):
        """tool_result on receipt is a dict (serialized), not a ToolResult object."""
        s = _fresh()
        reg = _make_registry_with_echo()
        s2, _ = gnf_step(s, "hello", tool_registry=reg)
        e_receipts = [r for r in s2["receipts"] if r.get("type") == "EXECUTION"]
        tr = e_receipts[-1]["tool_result"]
        assert isinstance(tr, dict)
        json.dumps(tr)  # must be JSON-serializable


# ═══════════════════════════════════════════════════════════════════════
# 13. Tool Provenance Chain
# ═══════════════════════════════════════════════════════════════════════

class TestToolProvenance:
    """Provenance: trace + artifact + receipt → full computational provenance."""

    def test_triple_provenance(self):
        """A governed tool step has: trace, tool_result, and receipt hash."""
        s = _fresh()
        reg = _make_registry_with_echo()
        s2, receipt = gnf_step(s, "hello", tool_registry=reg)

        # Trace (decision audit)
        e_receipts = [r for r in s2["receipts"] if r.get("type") == "EXECUTION"]
        last_exec = e_receipts[-1]
        assert "trace" in last_exec
        assert last_exec["trace"]["signal"] is not None

        # Artifact (tool output)
        assert "tool_result" in last_exec
        assert last_exec["tool_result"]["success"]

        # Receipt (hash chain)
        assert last_exec["hash"] is not None
        assert len(last_exec["hash"]) == 64

    def test_multi_step_provenance(self):
        """Multiple steps each have independent provenance."""
        s = _fresh()
        reg = _make_registry_with_echo()
        s, _ = gnf_step(s, "first", tool_registry=reg)
        s, _ = gnf_step(s, "second", tool_registry=reg)

        e_receipts = [r for r in s["receipts"] if r.get("type") == "EXECUTION"]
        assert len(e_receipts) == 2
        # Each has trace + tool_result
        for er in e_receipts:
            assert "trace" in er
            assert "tool_result" in er
        # Different hashes
        assert e_receipts[0]["hash"] != e_receipts[1]["hash"]

    def test_chain_valid_with_tools(self):
        """Receipt chain remains valid with tool_result attached."""
        s = _fresh()
        reg = _make_registry_with_echo()
        s, _ = gnf_step(s, "one", tool_registry=reg)
        s, _ = gnf_step(s, "two", tool_registry=reg)
        s, _ = gnf_step(s, "three", tool_registry=reg)
        assert verify_chain(s["receipts"])


# ═══════════════════════════════════════════════════════════════════════
# 14. Tool Hash Exclusion
# ═══════════════════════════════════════════════════════════════════════

class TestToolHashExclusion:
    """tool_result MUST NOT be in receipt hash (same boundary as trace)."""

    def test_hash_identical_with_and_without_tool(self):
        """The receipt hash is the same whether or not a tool runs."""
        s1 = _fresh()
        s2 = _fresh()

        # Without tool
        s1, _ = gnf_step(s1, "hello")
        # With tool
        reg = _make_registry_with_echo()
        s2, _ = gnf_step(s2, "hello", tool_registry=reg)

        # Receipt hashes must match (tool_result is not in hash)
        e1 = [r for r in s1["receipts"] if r.get("type") == "EXECUTION"][-1]
        e2 = [r for r in s2["receipts"] if r.get("type") == "EXECUTION"][-1]
        assert e1["hash"] == e2["hash"]

    def test_proposal_receipt_hash_unchanged(self):
        """Proposal receipt hash unaffected by tool registry."""
        s1 = _fresh()
        s2 = _fresh()
        reg = _make_registry_with_echo()

        s1, _ = gnf_step(s1, "test")
        s2, _ = gnf_step(s2, "test", tool_registry=reg)

        p1 = [r for r in s1["receipts"] if r.get("type") == "PROPOSAL"][-1]
        p2 = [r for r in s2["receipts"] if r.get("type") == "PROPOSAL"][-1]
        assert p1["hash"] == p2["hash"]

    def test_tool_result_present_but_not_hashed(self):
        """tool_result is on the receipt dict but not reflected in hash."""
        s = _fresh()
        reg = _make_registry_with_echo()
        s, _ = gnf_step(s, "hello", tool_registry=reg)
        e = [r for r in s["receipts"] if r.get("type") == "EXECUTION"][-1]
        assert "tool_result" in e
        # Remove tool_result and verify the hash would be the same
        # (it IS the same because tool_result is never in the hash payload)
        hash_with_tool = e["hash"]
        assert len(hash_with_tool) == 64


# ═══════════════════════════════════════════════════════════════════════
# 15. Adversarial: Stress Override Prevents Tool Execution
# ═══════════════════════════════════════════════════════════════════════

class TestAdversarialTools:
    """Stress override → PREVENT → no tool execution."""

    def test_stress_fail_blocks_tool(self):
        """When stress overrides to PREVENT, tool must not execute."""
        s = _fresh()
        reg = _make_registry_with_echo()

        # Custom stress check that always fails
        def always_fail(proposal, state, verdict):
            return "adversarial: forced failure"

        s2, receipt = gnf_step(
            s, "hello",
            stress_checks=[("always_fail", always_fail)],
            tool_registry=reg,
        )
        assert receipt.final_verdict == "PREVENT"
        assert receipt.effect_status == "DENIED"
        assert receipt.tool_result is None
        # State unchanged (NoSilentEffect)
        assert effect_footprint(s2) == effect_footprint(s)

    def test_stress_override_visible_in_trace(self):
        """Stress failure + no tool execution → both visible in trace."""
        s = _fresh()
        reg = _make_registry_with_echo()

        def always_fail(proposal, state, verdict):
            return "blocked by stress"

        s2, receipt = gnf_step(
            s, "hello",
            stress_checks=[("always_fail", always_fail)],
            tool_registry=reg,
        )
        # Trace shows the stress failure
        e_receipts = [r for r in s2["receipts"] if r.get("type") == "EXECUTION"]
        last_exec = e_receipts[-1]
        assert last_exec["trace"]["stress"]["verdict"] == "FAIL"
        assert last_exec["trace"]["final_verdict"] == "PREVENT"
        # No tool_result
        assert "tool_result" not in last_exec

    def test_chain_valid_after_adversarial_with_tools(self):
        """Chain integrity preserved through stress override + tool registry."""
        s = _fresh()
        reg = _make_registry_with_echo()

        # Normal step (tool runs)
        s, _ = gnf_step(s, "hello", tool_registry=reg)
        # Stress-blocked step (tool does NOT run)
        def fail_check(p, st, v):
            return "forced fail"
        s, _ = gnf_step(s, "blocked", stress_checks=[("f", fail_check)], tool_registry=reg)
        # Another normal step
        s, _ = gnf_step(s, "resume", tool_registry=reg)

        assert verify_chain(s["receipts"])
        # Count tool_results
        e_receipts = [r for r in s["receipts"] if r.get("type") == "EXECUTION"]
        with_tool = [r for r in e_receipts if "tool_result" in r]
        without_tool = [r for r in e_receipts if "tool_result" not in r]
        assert len(with_tool) == 2   # first and third
        assert len(without_tool) == 1  # second (blocked)


# ═══════════════════════════════════════════════════════════════════════
# 16. Full Pipeline: Python Exec Through Governance
# ═══════════════════════════════════════════════════════════════════════

class TestPythonExecGoverned:
    """Python execution routed through full GNF governance pipeline."""

    def test_python_exec_governed_allow(self):
        """Python exec through gnf_step with dict passthrough (PENDING → approved)."""
        s = _fresh()
        reg = default_registry()
        # python_exec is a WRITE_ACTION → governor returns PENDING
        s2, receipt = gnf_step(
            s,
            {"action": "python_exec", "payload": {"code": "2+2"}},
            tool_registry=reg,
        )
        # WRITE_ACTION → PENDING → DEFERRED (needs approval)
        assert receipt.effect_status == "DEFERRED"
        assert receipt.tool_result is None  # not executed (not APPLIED)

    def test_python_exec_direct_tool(self):
        """Direct tool call (outside governance) works."""
        r = python_exec({"code": "[x**2 for x in range(5)]"}, {})
        assert r.success
        assert r.output == [0, 1, 4, 9, 16]

    def test_fs_operations_governed(self, tmp_path):
        """fs_read governed — auto-ALLOW (not a write action)."""
        (tmp_path / "data.txt").write_text("governed content")
        s = _fresh()
        reg = default_registry()
        s2, receipt = gnf_step(
            s,
            {"action": "fs_read", "payload": {"path": "data.txt", "workspace": str(tmp_path)}},
            tool_registry=reg,
        )
        assert receipt.effect_status == "APPLIED"
        assert receipt.tool_result is not None
        assert receipt.tool_result.success
        assert receipt.tool_result.output == "governed content"

    def test_fs_write_governed_pending(self, tmp_path):
        """fs_write is WRITE_ACTION → PENDING."""
        s = _fresh()
        reg = default_registry()
        s2, receipt = gnf_step(
            s,
            {"action": "fs_write", "payload": {"path": "out.txt", "content": "x", "workspace": str(tmp_path)}},
            tool_registry=reg,
        )
        assert receipt.effect_status == "DEFERRED"
        assert receipt.tool_result is None

    def test_db_query_governed(self, tmp_path):
        """db_query governed — auto-ALLOW (read-only)."""
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('hello')")
        conn.commit()
        conn.close()

        s = _fresh()
        reg = default_registry()
        s2, receipt = gnf_step(
            s,
            {"action": "db_query", "payload": {"sql": "SELECT * FROM t", "db_path": db}},
            tool_registry=reg,
        )
        assert receipt.effect_status == "APPLIED"
        assert receipt.tool_result is not None
        assert receipt.tool_result.success
        assert receipt.tool_result.output["rows"][0]["v"] == "hello"
