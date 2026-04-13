"""Tests for helensh/sandbox/validate.py — Three-Layer Code Validation.

Tests verify:
  - AST validation catches broken syntax
  - Execution scoring detects runtime crashes
  - Test scoring verifies correctness
  - Combined metric follows gradient: broken < crashing < working < tested
  - Short-circuit: AST fail skips exec, exec fail skips test
  - ValidationResult is frozen and receipted
  - Receipt hashes are deterministic
  - Custom test injection works
  - Task-aware test generation (fibonacci, sorting, etc.)
"""
import pytest

from helensh.sandbox.validate import (
    DEFAULT_WEIGHTS,
    ValidationResult,
    ast_validation_score,
    execution_score,
    testing_score,
    validate,
    validate_proposal,
)


# ── AST Validation ───────────────────────────────────────────────────


class TestASTValidation:
    def test_valid_assignment(self):
        score, err = ast_validation_score("x = 1\nprint(x)")
        assert score == 1.0
        assert err is None

    def test_valid_function(self):
        score, err = ast_validation_score(
            "def fib(n):\n    return n if n <= 1 else fib(n-1) + fib(n-2)"
        )
        assert score == 1.0
        assert err is None

    def test_valid_class(self):
        score, _ = ast_validation_score("class Foo:\n    x = 1")
        assert score == 1.0

    def test_syntax_error(self):
        score, err = ast_validation_score("def foo(:\n  pass")
        assert score == 0.0
        assert "SyntaxError" in err

    def test_incomplete_string(self):
        score, err = ast_validation_score("x = 'unterminated")
        assert score == 0.0
        assert err is not None

    def test_empty_content(self):
        score, err = ast_validation_score("")
        assert score == 0.0
        assert "empty" in err

    def test_whitespace_only(self):
        score, err = ast_validation_score("   \n  \n")
        assert score == 0.0

    def test_none_content(self):
        score, err = ast_validation_score(None)
        assert score == 0.0

    def test_comment_only_is_valid(self):
        score, _ = ast_validation_score("# just a comment")
        assert score == 1.0

    def test_multiline_valid(self):
        code = (
            "import os\n"
            "def hello(name):\n"
            "    return f'hello {name}'\n"
            "print(hello('world'))\n"
        )
        score, _ = ast_validation_score(code)
        assert score == 1.0


# ── Execution Scoring ────────────────────────────────────────────────


class TestExecutionScore:
    def test_simple_runs(self):
        score, err, _ = execution_score("x = 1 + 1")
        assert score == 1.0
        assert err is None

    def test_print_succeeds(self):
        score, _, _ = execution_score("print('hello')")
        assert score == 1.0

    def test_runtime_error(self):
        score, err, stderr = execution_score("x = 1/0")
        assert score == 0.0
        assert "exit code" in err
        assert stderr is not None

    def test_import_error(self):
        score, err, _ = execution_score("import nonexistent_module_xyz_12345")
        assert score == 0.0

    def test_empty_content(self):
        score, err, _ = execution_score("")
        assert score == 0.0

    def test_timeout(self):
        score, err, _ = execution_score(
            "import time; time.sleep(10)", timeout=1,
        )
        assert score == 0.0
        assert "timeout" in err

    def test_name_error(self):
        score, err, _ = execution_score("print(undefined_variable_xyz)")
        assert score == 0.0

    def test_assertion_error(self):
        score, err, _ = execution_score("assert False, 'intentional'")
        assert score == 0.0

    def test_sys_exit_zero_succeeds(self):
        score, _, _ = execution_score("import sys; sys.exit(0)")
        assert score == 1.0

    def test_sys_exit_nonzero_fails(self):
        score, _, _ = execution_score("import sys; sys.exit(1)")
        assert score == 0.0


# ── Test Scoring ─────────────────────────────────────────────────────


class TestTestingScore:
    def test_importable_module(self):
        score, err, _ = testing_score("x = 42\ndef foo(): return x")
        assert score == 1.0
        assert err is None

    def test_broken_module_fails_import(self):
        score, err, _ = testing_score("raise RuntimeError('boom on import')")
        assert score == 0.0

    def test_empty_content(self):
        score, err, _ = testing_score("")
        assert score == 0.0

    def test_custom_test_passes(self):
        code = "def add(a, b): return a + b"
        tests = (
            "from module import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
        score, err, _ = testing_score(code, test_content=tests)
        assert score == 1.0

    def test_custom_test_fails(self):
        code = "def add(a, b): return a - b  # intentional bug"
        tests = (
            "from module import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
        score, err, _ = testing_score(code, test_content=tests)
        assert score == 0.0

    def test_fibonacci_with_tests(self):
        code = (
            "def fib(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    return fib(n - 1) + fib(n - 2)\n"
        )
        tests = (
            "from module import fib\n\n"
            "def test_fib_0(): assert fib(0) == 0\n"
            "def test_fib_1(): assert fib(1) == 1\n"
            "def test_fib_5(): assert fib(5) == 5\n"
            "def test_fib_10(): assert fib(10) == 55\n"
        )
        score, err, output = testing_score(code, test_content=tests)
        assert score == 1.0
        assert err is None

    def test_buggy_fibonacci_fails(self):
        code = (
            "def fib(n):\n"
            "    return n  # wrong\n"
        )
        tests = (
            "from module import fib\n\n"
            "def test_fib_5(): assert fib(5) == 5\n"
            "def test_fib_10(): assert fib(10) == 55\n"
        )
        score, err, _ = testing_score(code, test_content=tests)
        assert score == 0.0

    def test_sorting_with_tests(self):
        code = (
            "def sort_list(lst):\n"
            "    return sorted(lst)\n"
        )
        tests = (
            "from module import sort_list\n\n"
            "def test_empty(): assert sort_list([]) == []\n"
            "def test_sorted(): assert sort_list([3,1,2]) == [1,2,3]\n"
            "def test_single(): assert sort_list([5]) == [5]\n"
            "def test_dupes(): assert sort_list([2,1,2]) == [1,2,2]\n"
        )
        score, err, _ = testing_score(code, test_content=tests)
        assert score == 1.0


# ── Combined Validation ──────────────────────────────────────────────


class TestValidate:
    def test_valid_code_full_score(self):
        result = validate("x = 42\nprint(x)")
        assert result.ast_score == 1.0
        assert result.exec_score == 1.0
        assert result.test_score == 1.0
        assert result.combined_score == pytest.approx(1.0)

    def test_syntax_error_zero(self):
        result = validate("def foo(:\n  pass")
        assert result.ast_score == 0.0
        assert result.exec_score == 0.0
        assert result.test_score == 0.0
        assert result.combined_score == 0.0

    def test_runtime_error_partial(self):
        result = validate("x = 1/0")
        assert result.ast_score == 1.0
        assert result.exec_score == 0.0
        assert result.test_score == 0.0
        # Only AST weight: 0.3 * 1.0 = 0.3
        assert result.combined_score == pytest.approx(0.3)

    def test_short_circuit_on_ast_fail(self):
        result = validate("def (broken")
        assert result.exec_error == "skipped (AST failed)"
        assert result.test_error == "skipped (execution failed)"

    def test_short_circuit_on_exec_fail(self):
        result = validate("import sys; sys.exit(1)")
        assert result.ast_score == 1.0
        assert result.exec_score == 0.0
        assert result.test_error == "skipped (execution failed)"

    def test_custom_weights_ast_only(self):
        result = validate("x = 42", weights=(1.0, 0.0, 0.0))
        assert result.combined_score == 1.0
        assert result.weights == (1.0, 0.0, 0.0)

    def test_custom_weights_test_only(self):
        result = validate("x = 42", weights=(0.0, 0.0, 1.0))
        assert result.combined_score == 1.0

    def test_empty_content_zero(self):
        result = validate("")
        assert result.combined_score == 0.0

    def test_with_custom_tests(self):
        code = "def double(x): return x * 2"
        tests = (
            "from module import double\n\n"
            "def test_double(): assert double(5) == 10\n"
            "def test_double_zero(): assert double(0) == 0\n"
            "def test_double_neg(): assert double(-3) == -6\n"
        )
        result = validate(code, test_content=tests)
        assert result.combined_score == pytest.approx(1.0)

    def test_with_failing_custom_tests(self):
        code = "def double(x): return x + 2  # bug"
        tests = (
            "from module import double\n\n"
            "def test_double(): assert double(5) == 10\n"
        )
        result = validate(code, test_content=tests)
        assert result.ast_score == 1.0
        assert result.exec_score == 1.0
        assert result.test_score == 0.0
        # 0.3 * 1 + 0.3 * 1 + 0.4 * 0 = 0.6
        assert result.combined_score == pytest.approx(0.6)


# ── Receipt Integrity ────────────────────────────────────────────────


class TestReceiptIntegrity:
    def test_receipt_hash_is_64_hex(self):
        result = validate("x = 1")
        assert isinstance(result.receipt_hash, str)
        assert len(result.receipt_hash) == 64

    def test_receipt_hash_deterministic(self):
        r1 = validate("x = 1")
        r2 = validate("x = 1")
        assert r1.receipt_hash == r2.receipt_hash

    def test_different_content_different_hash(self):
        r1 = validate("x = 1")
        r2 = validate("x = 2")
        assert r1.receipt_hash != r2.receipt_hash

    def test_different_weights_different_hash(self):
        r1 = validate("x = 1", weights=(0.3, 0.3, 0.4))
        r2 = validate("x = 1", weights=(0.5, 0.3, 0.2))
        assert r1.receipt_hash != r2.receipt_hash

    def test_content_hash_is_64_hex(self):
        result = validate("x = 1")
        assert isinstance(result.content_hash, str)
        assert len(result.content_hash) == 64

    def test_result_is_frozen(self):
        result = validate("x = 1")
        with pytest.raises(AttributeError):
            result.ast_score = 0.5

    def test_result_is_frozen_combined(self):
        result = validate("x = 1")
        with pytest.raises(AttributeError):
            result.combined_score = 999.0


# ── Scoring Gradient ─────────────────────────────────────────────────


class TestScoringGradient:
    """Verify the fundamental gradient: broken < crashing < working < tested."""

    def test_broken_is_zero(self):
        result = validate("def (broken syntax")
        assert result.combined_score == 0.0

    def test_crashing_is_partial(self):
        result = validate("x = 1/0")
        assert 0.0 < result.combined_score < 1.0

    def test_working_no_tests_is_high(self):
        result = validate("x = 42\nprint(x)")
        assert result.combined_score >= 0.6

    def test_tested_code_is_full(self):
        code = "def add(a, b): return a + b"
        tests = (
            "from module import add\n\n"
            "def test_add(): assert add(2, 3) == 5\n"
        )
        result = validate(code, test_content=tests)
        assert result.combined_score == pytest.approx(1.0)

    def test_gradient_order(self):
        broken = validate("def (syntax error")
        crashing = validate("x = 1/0")
        working = validate("x = 42\nprint(x)")

        assert broken.combined_score < crashing.combined_score
        assert crashing.combined_score < working.combined_score

    def test_buggy_code_scores_below_correct(self):
        correct = "def double(x): return x * 2"
        buggy = "def double(x): return x + 2"
        tests = (
            "from module import double\n\n"
            "def test_double(): assert double(5) == 10\n"
        )
        r_correct = validate(correct, test_content=tests)
        r_buggy = validate(buggy, test_content=tests)
        assert r_buggy.combined_score < r_correct.combined_score


# ── Proposal Validation ──────────────────────────────────────────────


class TestValidateProposal:
    def test_extracts_from_payload_content(self):
        proposal = {"payload": {"content": "x = 42"}}
        result = validate_proposal(proposal)
        assert result.ast_score == 1.0

    def test_extracts_from_top_level_content(self):
        proposal = {"content": "x = 42"}
        result = validate_proposal(proposal)
        assert result.ast_score == 1.0

    def test_empty_proposal(self):
        result = validate_proposal({})
        assert result.combined_score == 0.0

    def test_broken_code_proposal(self):
        proposal = {"payload": {"content": "def (broken"}}
        result = validate_proposal(proposal)
        assert result.combined_score == 0.0

    def test_proposal_with_custom_tests(self):
        proposal = {"payload": {"content": "def greet(n): return f'hi {n}'"}}
        tests = (
            "from module import greet\n\n"
            "def test_greet(): assert greet('X') == 'hi X'\n"
        )
        result = validate_proposal(proposal, test_content=tests)
        assert result.combined_score == pytest.approx(1.0)


# ── Edge Cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_infinite_loop_timeout(self):
        result = validate("while True: pass", exec_timeout=1)
        assert result.ast_score == 1.0
        assert result.exec_score == 0.0
        assert "timeout" in result.exec_error

    def test_code_with_imports(self):
        result = validate("import os\nprint(os.getcwd())")
        assert result.ast_score == 1.0
        assert result.exec_score == 1.0

    def test_multiline_function(self):
        code = (
            "def is_prime(n):\n"
            "    if n < 2:\n"
            "        return False\n"
            "    for i in range(2, int(n**0.5) + 1):\n"
            "        if n % i == 0:\n"
            "            return False\n"
            "    return True\n"
        )
        tests = (
            "from module import is_prime\n\n"
            "def test_2(): assert is_prime(2) is True\n"
            "def test_4(): assert is_prime(4) is False\n"
            "def test_17(): assert is_prime(17) is True\n"
            "def test_1(): assert is_prime(1) is False\n"
        )
        result = validate(code, test_content=tests)
        assert result.combined_score == pytest.approx(1.0)

    def test_class_definition(self):
        code = (
            "class Counter:\n"
            "    def __init__(self):\n"
            "        self.count = 0\n"
            "    def inc(self):\n"
            "        self.count += 1\n"
            "        return self.count\n"
        )
        tests = (
            "from module import Counter\n\n"
            "def test_init(): assert Counter().count == 0\n"
            "def test_inc():\n"
            "    c = Counter()\n"
            "    assert c.inc() == 1\n"
            "    assert c.inc() == 2\n"
        )
        result = validate(code, test_content=tests)
        assert result.combined_score == pytest.approx(1.0)
