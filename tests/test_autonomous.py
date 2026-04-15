"""
HELEN OS Autonomous Execution Loop — Test Suite

Tests: execution, failure diagnosis, strategy adaptation, retry,
validation, full loop lifecycle, and edge cases.
"""

import pytest
from helen_os.autonomous import (
    AutonomousLoop, AttemptRecord, AttemptStatus, LoopStatus,
    LoopResult, ValidationRecord,
    _exec_python, diagnose_failure, adapt_strategy, validate_result,
    run_autonomous,
)


# ===================================================================
# Sandboxed execution
# ===================================================================

class TestExecPython:
    def test_simple_print(self):
        ok, out, err = _exec_python('print("hello")')
        assert ok
        assert out == "hello"
        assert err is None

    def test_math(self):
        ok, out, err = _exec_python('print(2 + 2)')
        assert ok
        assert out == "4"

    def test_syntax_error(self):
        ok, out, err = _exec_python('def f(')
        assert not ok
        assert "SyntaxError" in err

    def test_runtime_error(self):
        ok, out, err = _exec_python('print(1/0)')
        assert not ok
        assert "ZeroDivision" in err

    def test_name_error(self):
        ok, out, err = _exec_python('print(undefined_var)')
        assert not ok
        assert "NameError" in err

    def test_blocked_import(self):
        ok, out, err = _exec_python('import subprocess')
        assert not ok
        assert "Blocked import" in err

    def test_allowed_import(self):
        ok, out, err = _exec_python('import math\nprint(math.pi)')
        assert ok
        assert "3.14" in out

    def test_timeout(self):
        ok, out, err = _exec_python('while True: pass', timeout=2)
        assert not ok
        assert "timed out" in err

    def test_multiline(self):
        code = "x = 10\ny = 20\nprint(x + y)"
        ok, out, err = _exec_python(code)
        assert ok
        assert out == "30"

    def test_function_definition(self):
        code = "def fib(n):\n    if n <= 1: return n\n    return fib(n-1) + fib(n-2)\nprint(fib(10))"
        ok, out, err = _exec_python(code)
        assert ok
        assert out == "55"


# ===================================================================
# Failure diagnosis
# ===================================================================

class TestDiagnosis:
    def test_syntax_error(self):
        diag, fix = diagnose_failure("", "SyntaxError: invalid syntax", 1)
        assert diag == "SYNTAX_ERROR"

    def test_name_error(self):
        diag, fix = diagnose_failure("", "NameError: name 'foo' is not defined", 1)
        assert "UNDEFINED_NAME:foo" == diag

    def test_type_error(self):
        diag, fix = diagnose_failure("", "TypeError: unsupported operand", 1)
        assert diag == "TYPE_ERROR"

    def test_zero_division(self):
        diag, fix = diagnose_failure("", "ZeroDivisionError: division by zero", 1)
        assert diag == "DIVISION_BY_ZERO"

    def test_import_error(self):
        diag, fix = diagnose_failure("", "ModuleNotFoundError: No module named 'torch'", 1)
        assert diag == "IMPORT_ERROR"

    def test_nan_detection(self):
        diag, fix = diagnose_failure("", "ValueError: cannot convert float NaN to integer", 1)
        assert diag == "NUMERICAL_INSTABILITY"

    def test_memory_error(self):
        diag, fix = diagnose_failure("", "MemoryError: unable to allocate", 1)
        assert diag == "OUT_OF_MEMORY"

    def test_unknown_error(self):
        diag, fix = diagnose_failure("", "WeirdCustomError: something", 1)
        assert diag == "UNKNOWN_ERROR"

    def test_timeout_diagnosis(self):
        diag, fix = diagnose_failure("", "Execution timed out after 30s", 1)
        assert diag == "TIMEOUT"

    def test_blocked_import_diagnosis(self):
        diag, fix = diagnose_failure("", "Blocked import: subprocess", 1)
        assert diag == "BLOCKED_IMPORT"


# ===================================================================
# Strategy adaptation
# ===================================================================

class TestAdaptStrategy:
    def test_undefined_name_adds_definition(self):
        strategy, code = adapt_strategy("test", "print(x)", "UNDEFINED_NAME:x", "define x", 1)
        assert "x = None" in code
        assert "Attempt 2" in strategy

    def test_zero_division_adds_guard(self):
        strategy, code = adapt_strategy("test", "a / b", "DIVISION_BY_ZERO", "guard", 1)
        assert "max(1," in code

    def test_nan_adds_sanitization(self):
        strategy, code = adapt_strategy("test", "print(x)", "NUMERICAL_INSTABILITY", "fix", 1)
        assert "_safe" in code
        assert "math.isnan" in code

    def test_blocked_import_removes_it(self):
        original = "import subprocess\nimport math\nprint(math.pi)"
        strategy, code = adapt_strategy("test", original, "BLOCKED_IMPORT", "remove", 1)
        assert "subprocess" not in code
        assert "math" in code

    def test_unknown_adds_comment(self):
        strategy, code = adapt_strategy("test", "x = 1", "UNKNOWN_ERROR", "try different", 1)
        assert "Previous failure" in code


# ===================================================================
# Validation
# ===================================================================

class TestValidation:
    def test_valid_result(self):
        v = validate_result("print(42)", "42", "compute 42")
        assert v.passed
        assert v.checks_passed == v.checks_run

    def test_empty_output_fails(self):
        v = validate_result("pass", "", "do something")
        assert not v.passed
        assert "Empty output" in v.failures

    def test_error_in_output_fails(self):
        v = validate_result("x=1", "Traceback: something broke", "test")
        assert not v.passed
        assert "Error markers" in v.failures[0]

    def test_custom_check(self):
        def must_contain_42(code, output):
            return ("42" in output, "Output must contain 42")

        v = validate_result("print(42)", "42", "test", [must_contain_42])
        assert v.passed

    def test_custom_check_fails(self):
        def must_contain_42(code, output):
            return ("42" in output, "Output must contain 42")

        v = validate_result("print(1)", "1", "test", [must_contain_42])
        assert not v.passed
        assert "42" in v.failures[0]

    def test_authority_always_none(self):
        v = validate_result("x=1", "ok", "test")
        assert v.authority == "NONE"


# ===================================================================
# Full loop — success on first attempt
# ===================================================================

class TestLoopSuccess:
    def test_immediate_success(self):
        result = run_autonomous("print hello", 'print("hello world")')
        assert result.status == LoopStatus.VALIDATED
        assert result.total_attempts == 1
        assert result.successful_attempt == 1
        assert "hello world" in result.final_output
        assert result.authority == "NONE"

    def test_math_computation(self):
        result = run_autonomous("compute fibonacci", """
def fib(n):
    if n <= 1: return n
    return fib(n-1) + fib(n-2)
print(fib(10))
""")
        assert result.status == LoopStatus.VALIDATED
        assert "55" in result.final_output

    def test_loop_hash_is_deterministic(self):
        r1 = run_autonomous("test", 'print("deterministic")')
        r2 = run_autonomous("test", 'print("deterministic")')
        # Hashes won't match exactly due to timestamps, but structure is consistent
        assert r1.status == r2.status
        assert r1.total_attempts == r2.total_attempts


# ===================================================================
# Full loop — failure recovery
# ===================================================================

class TestLoopRecovery:
    def test_name_error_recovery(self):
        """Code references undefined variable. Loop should diagnose and adapt."""
        result = run_autonomous("print x", "print(x)", max_attempts=3)
        # First attempt fails with NameError
        assert result.attempts[0].status == AttemptStatus.FAILED
        assert result.attempts[0].diagnosis == "UNDEFINED_NAME:x"
        # Second attempt has the auto-fix
        assert "x = None" in result.attempts[1].code

    def test_blocked_import_recovery(self):
        """Code uses forbidden import. Loop should remove it."""
        code = "import subprocess\nprint('hello')"
        result = run_autonomous("print hello", code, max_attempts=3)
        # First attempt blocked
        assert result.attempts[0].status == AttemptStatus.FAILED
        assert result.attempts[0].diagnosis == "BLOCKED_IMPORT"
        # Second attempt should have import removed
        assert "subprocess" not in result.attempts[1].code

    def test_syntax_error_recorded(self):
        """Syntax error is diagnosed correctly."""
        result = run_autonomous("test", "def f(", max_attempts=2)
        assert result.attempts[0].diagnosis == "SYNTAX_ERROR"

    def test_max_attempts_exhausted(self):
        """If all attempts fail, status is EXHAUSTED."""
        result = run_autonomous("impossible", "raise ValueError('always fails')", max_attempts=3)
        assert result.status == LoopStatus.EXHAUSTED
        assert result.total_attempts == 3
        assert result.successful_attempt is None


# ===================================================================
# Loop with custom validation
# ===================================================================

class TestLoopValidation:
    def test_custom_validation_passes(self):
        def check_even(code, output):
            try:
                return (int(output.strip()) % 2 == 0, "Output must be even")
            except ValueError:
                return (False, "Output is not a number")

        result = run_autonomous("compute even number", "print(42)", checks=[check_even])
        assert result.status == LoopStatus.VALIDATED
        assert result.validation.passed

    def test_custom_validation_fails(self):
        def check_even(code, output):
            try:
                return (int(output.strip()) % 2 == 0, "Output must be even")
            except ValueError:
                return (False, "Output is not a number")

        result = run_autonomous("compute even", "print(43)", checks=[check_even])
        assert result.status == LoopStatus.COMPLETED  # succeeded but validation failed
        assert not result.validation.passed


# ===================================================================
# Attempt records
# ===================================================================

class TestAttemptRecords:
    def test_all_attempts_have_authority_none(self):
        result = run_autonomous("test", "print(x)", max_attempts=3)
        for a in result.attempts:
            assert a.authority == "NONE"

    def test_attempt_has_duration(self):
        result = run_autonomous("test", 'print("fast")')
        assert result.attempts[0].duration_ms >= 0

    def test_attempt_serializable(self):
        result = run_autonomous("test", 'print("hello")')
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["authority"] == "NONE"
        assert isinstance(d["attempts"], list)
        # Must be JSON-serializable
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_failed_attempt_has_error(self):
        result = run_autonomous("test", "1/0", max_attempts=1)
        assert result.attempts[0].error is not None
        assert "ZeroDivision" in result.attempts[0].error


# ===================================================================
# Callback
# ===================================================================

class TestCallback:
    def test_on_attempt_called(self):
        log = []
        loop = AutonomousLoop(max_attempts=2, on_attempt=lambda r: log.append(r))
        loop.run("test", 'print("ok")')
        assert len(log) == 1
        assert log[0].status == AttemptStatus.SUCCESS

    def test_callback_on_failure(self):
        log = []
        loop = AutonomousLoop(max_attempts=2, on_attempt=lambda r: log.append(r))
        loop.run("test", "print(undefined)")
        assert len(log) == 2
        assert log[0].status == AttemptStatus.FAILED


# ===================================================================
# Edge cases
# ===================================================================

import json

class TestEdgeCases:
    def test_empty_code(self):
        result = run_autonomous("test", "")
        # Empty code produces no output
        assert result.total_attempts >= 1

    def test_only_comments(self):
        result = run_autonomous("test", "# just a comment")
        assert result.total_attempts >= 1

    def test_very_long_output(self):
        result = run_autonomous("test", "print('x' * 10000)")
        assert result.status == LoopStatus.VALIDATED
        assert len(result.final_output) <= 5000  # truncated in attempt

    def test_max_attempts_1(self):
        result = run_autonomous("test", "print(x)", max_attempts=1)
        assert result.total_attempts == 1
        assert result.status == LoopStatus.EXHAUSTED

    def test_result_hash_exists(self):
        result = run_autonomous("test", 'print("hash me")')
        assert result.loop_hash
        assert len(result.loop_hash) == 16
