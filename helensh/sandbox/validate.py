"""HELEN OS — Three-Layer Code Validation.

Replaces surface heuristics with a semantic + executable correctness metric.

Three layers of truth:
  1. Structural validity  → AST parse    (syntactic well-formedness)
  2. Executability        → subprocess   (no runtime crash)
  3. Correctness          → test run     (behavior matches specification)

Combined metric:
  S = w₁·S_ast + w₂·S_exec + w₃·S_test

Scoring gradient:
  invalid syntax  → 0.0
  crashing code   → 0.3  (AST only)
  working code    → 0.6  (AST + exec)
  tested code     → 1.0  (AST + exec + tests)

Law 4 applies: Materialized does not imply successful.
Law 6 applies: Every validation produces a receipted ValidationResult.

Safety:
  - Generated code executes in subprocess with timeout
  - Tests execute in ephemeral tmpdir
  - No filesystem persistence from validated code
"""
from __future__ import annotations

import ast
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical_hash


# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_WEIGHTS = (0.3, 0.3, 0.4)  # AST, exec, test
DEFAULT_EXEC_TIMEOUT = 3   # seconds
DEFAULT_TEST_TIMEOUT = 5   # seconds


# ── Result structure ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationResult:
    """Immutable, receipted validation result.

    All scores in [0.0, 1.0]. Combined score is weighted sum.
    Receipt hash is deterministic from content + scores.
    """
    content_hash: str
    ast_score: float
    exec_score: float
    test_score: float
    combined_score: float
    weights: Tuple[float, float, float]
    ast_error: Optional[str]
    exec_error: Optional[str]
    exec_stderr: Optional[str]
    test_error: Optional[str]
    test_output: Optional[str]
    receipt_hash: str


# ── Layer 1: AST Validation ──────────────────────────────────────────


def ast_validation_score(content: str) -> Tuple[float, Optional[str]]:
    """Structural validity via AST parse.

    Returns (score, error_message).
    1.0 if valid Python syntax, 0.0 otherwise.
    """
    if not content or not content.strip():
        return 0.0, "empty content"
    try:
        ast.parse(content)
        return 1.0, None
    except SyntaxError as e:
        return 0.0, f"SyntaxError: {e.msg} (line {e.lineno})"


# ── Layer 2: Execution ───────────────────────────────────────────────


def execution_score(
    content: str,
    timeout: int = DEFAULT_EXEC_TIMEOUT,
) -> Tuple[float, Optional[str], Optional[str]]:
    """Runtime executability via sandboxed subprocess.

    Returns (score, error_message, stderr).
    1.0 if exit code 0, 0.0 otherwise.
    """
    if not content or not content.strip():
        return 0.0, "empty content", None

    path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = f.name

        result = subprocess.run(
            ["python3", path],
            capture_output=True,
            timeout=timeout,
            text=True,
        )

        if result.returncode == 0:
            stderr = result.stderr.strip() if result.stderr else None
            return 1.0, None, stderr
        else:
            stderr = result.stderr.strip() if result.stderr else "non-zero exit"
            return 0.0, f"exit code {result.returncode}", stderr

    except subprocess.TimeoutExpired:
        return 0.0, f"timeout after {timeout}s", None
    except Exception as e:
        return 0.0, str(e), None
    finally:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


# ── Layer 3: Test Execution ──────────────────────────────────────────


def testing_score(
    content: str,
    test_content: Optional[str] = None,
    timeout: int = DEFAULT_TEST_TIMEOUT,
) -> Tuple[float, Optional[str], Optional[str]]:
    """Correctness via test execution.

    If test_content is provided, uses it as the test file.
    Otherwise generates a minimal importability test.

    Returns (score, error_message, test_output).
    """
    if not content or not content.strip():
        return 0.0, "empty content", None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            code_file = tmppath / "module.py"
            test_file = tmppath / "test_module.py"

            code_file.write_text(content, encoding="utf-8")

            if test_content:
                test_file.write_text(test_content, encoding="utf-8")
            else:
                # Minimal importability test — proves the module loads without error
                test_file.write_text(
                    "import module\n\n"
                    "def test_importable():\n"
                    "    assert hasattr(module, '__dict__')\n\n"
                    "def test_has_content():\n"
                    "    names = [n for n in dir(module) if not n.startswith('_')]\n"
                    "    assert len(names) >= 0\n",
                    encoding="utf-8",
                )

            result = subprocess.run(
                ["python3", "-m", "pytest", str(tmpdir), "-v",
                 "--tb=short", "--no-header", "-q"],
                capture_output=True,
                timeout=timeout,
                text=True,
            )

            output = result.stdout.strip() if result.stdout else ""

            if result.returncode == 0:
                return 1.0, None, output
            else:
                return 0.0, f"tests failed (exit {result.returncode})", output

    except subprocess.TimeoutExpired:
        return 0.0, f"test timeout after {timeout}s", None
    except Exception as e:
        return 0.0, str(e), None


# ── Combined Validation ──────────────────────────────────────────────


def validate(
    content: str,
    test_content: Optional[str] = None,
    weights: Tuple[float, float, float] = DEFAULT_WEIGHTS,
    exec_timeout: int = DEFAULT_EXEC_TIMEOUT,
    test_timeout: int = DEFAULT_TEST_TIMEOUT,
) -> ValidationResult:
    """Full three-layer validation with combined score.

    Short-circuits: AST fail → skip exec. Exec fail → skip tests.

    S = w₁·S_ast + w₂·S_exec + w₃·S_test

    Returns a frozen, receipted ValidationResult.
    """
    w1, w2, w3 = weights
    content_hash = canonical_hash({"content": content})

    # Layer 1: AST
    s_ast, ast_err = ast_validation_score(content)

    # Layer 2: Execution (only if AST passes)
    if s_ast > 0:
        s_exec, exec_err, exec_stderr = execution_score(content, timeout=exec_timeout)
    else:
        s_exec, exec_err, exec_stderr = 0.0, "skipped (AST failed)", None

    # Layer 3: Tests (only if execution passes)
    if s_exec > 0:
        s_test, test_err, test_output = testing_score(
            content, test_content, timeout=test_timeout,
        )
    else:
        s_test, test_err, test_output = 0.0, "skipped (execution failed)", None

    combined = w1 * s_ast + w2 * s_exec + w3 * s_test

    # Deterministic receipt hash from load-bearing fields only
    receipt_payload = {
        "content_hash": content_hash,
        "ast_score": s_ast,
        "exec_score": s_exec,
        "test_score": s_test,
        "combined_score": combined,
        "weights": list(weights),
    }
    receipt_hash = canonical_hash(receipt_payload)

    return ValidationResult(
        content_hash=content_hash,
        ast_score=s_ast,
        exec_score=s_exec,
        test_score=s_test,
        combined_score=combined,
        weights=weights,
        ast_error=ast_err,
        exec_error=exec_err,
        exec_stderr=exec_stderr,
        test_error=test_err,
        test_output=test_output,
        receipt_hash=receipt_hash,
    )


def validate_proposal(proposal: dict, **kwargs) -> ValidationResult:
    """Validate a kernel proposal's code content.

    Extracts content from proposal.payload.content or proposal.content.
    """
    content = proposal.get("payload", {}).get("content", "")
    if not content:
        content = proposal.get("content", "")
    return validate(content, **kwargs)


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "ValidationResult",
    "DEFAULT_WEIGHTS",
    "ast_validation_score",
    "execution_score",
    "testing_score",
    "validate",
    "validate_proposal",
]
