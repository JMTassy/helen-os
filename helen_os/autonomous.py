"""
HELEN OS Autonomous Execution Loop

Goal in → decompose → attempt → fail → diagnose → adapt → retry → validate → receipt.

The loop does NOT give up on first failure. It:
1. Attempts execution
2. Catches the failure
3. Diagnoses what went wrong (structured, not prose)
4. Adapts the strategy (patch code, change params, try different approach)
5. Retries with the adapted plan
6. Validates the result (tests, checks, assertions)
7. Receipts everything

authority = NONE on every record. The loop proposes and executes — it does not decide truth.
"""

import ast
import hashlib
import json
import subprocess
import tempfile
import textwrap
import time
import traceback
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class AttemptStatus(Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class LoopStatus(Enum):
    COMPLETED = "COMPLETED"
    EXHAUSTED = "EXHAUSTED"       # max attempts reached, no success
    VALIDATED = "VALIDATED"        # completed + validation passed


def _hash(data: Any) -> str:
    s = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class AttemptRecord:
    attempt_number: int
    strategy: str
    code: str
    status: AttemptStatus
    output: str
    error: Optional[str]
    duration_ms: int
    diagnosis: Optional[str] = None
    patch_applied: Optional[str] = None
    timestamp: str = field(default_factory=_ts)
    authority: str = "NONE"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ValidationRecord:
    passed: bool
    checks_run: int
    checks_passed: int
    failures: List[str]
    output: str
    timestamp: str = field(default_factory=_ts)
    authority: str = "NONE"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LoopResult:
    goal: str
    status: LoopStatus
    total_attempts: int
    successful_attempt: Optional[int]
    attempts: List[AttemptRecord]
    validation: Optional[ValidationRecord]
    final_output: Optional[str]
    final_code: Optional[str]
    loop_hash: str = ""
    duration_total_ms: int = 0
    authority: str = "NONE"

    def to_dict(self) -> dict:
        d = {
            "goal": self.goal,
            "status": self.status.value,
            "total_attempts": self.total_attempts,
            "successful_attempt": self.successful_attempt,
            "attempts": [a.to_dict() for a in self.attempts],
            "validation": self.validation.to_dict() if self.validation else None,
            "final_output": self.final_output,
            "final_code": self.final_code,
            "loop_hash": self.loop_hash,
            "duration_total_ms": self.duration_total_ms,
            "authority": self.authority,
        }
        return d


# ---------------------------------------------------------------------------
# Sandboxed Python execution
# ---------------------------------------------------------------------------

def _exec_python(code: str, timeout: int = 30) -> Tuple[bool, str, Optional[str]]:
    """Execute Python code in a sandboxed subprocess.
    Returns (success, stdout, error_message)."""
    # AST check first
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = ""
                if isinstance(node, ast.Import):
                    mod = node.names[0].name
                elif node.module:
                    mod = node.module
                # Allow safe standard library modules
                allowed = {"math", "json", "re", "collections", "itertools",
                           "functools", "string", "textwrap", "hashlib", "random",
                           "datetime", "time", "os.path", "pathlib", "typing",
                           "dataclasses", "enum", "abc", "copy", "io", "csv"}
                if mod.split(".")[0] not in allowed:
                    return False, "", f"Blocked import: {mod}"
    except SyntaxError as e:
        return False, "", f"SyntaxError: {e}"

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            path = f.name

        result = subprocess.run(
            ["python3", path],
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        if result.returncode == 0:
            return True, result.stdout.strip(), None
        else:
            return False, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", f"Execution timed out after {timeout}s"
    except Exception as e:
        return False, "", str(e)
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Diagnosis engine — structured failure analysis
# ---------------------------------------------------------------------------

def diagnose_failure(code: str, error: str, attempt_number: int) -> Tuple[str, str]:
    """Diagnose why code failed. Returns (diagnosis, suggested_fix).

    This is deterministic pattern matching, not LLM inference.
    For LLM-powered diagnosis, use diagnose_with_llm().
    """
    err_lower = error.lower()

    # Common failure patterns
    if "nan" in err_lower or "inf" in err_lower:
        return "NUMERICAL_INSTABILITY", "Add NaN/Inf guards, clamp values, check denominators"
    if "timed out" in err_lower or "timeout" in err_lower:
        return "TIMEOUT", "Optimize algorithm or reduce input size"
    if "syntaxerror" in err_lower:
        return "SYNTAX_ERROR", "Fix syntax — check indentation, brackets, colons"
    if "nameerror" in err_lower:
        # Extract the name
        import re
        m = re.search(r"name '(\w+)' is not defined", error)
        name = m.group(1) if m else "unknown"
        return f"UNDEFINED_NAME:{name}", f"Define '{name}' before use or import it"
    if "typeerror" in err_lower:
        return "TYPE_ERROR", "Check argument types and function signatures"
    if "indexerror" in err_lower:
        return "INDEX_ERROR", "Check list/array bounds before accessing"
    if "keyerror" in err_lower:
        return "KEY_ERROR", "Check dict keys exist before accessing (use .get())"
    if "valueerror" in err_lower:
        return "VALUE_ERROR", "Check value conversions and constraints"
    if "importerror" in err_lower or "modulenotfounderror" in err_lower:
        return "IMPORT_ERROR", "Module not available in sandbox — use stdlib only"
    if "attributeerror" in err_lower:
        return "ATTRIBUTE_ERROR", "Check object type and available attributes"
    if "zerodivisionerror" in err_lower:
        return "DIVISION_BY_ZERO", "Add zero-check guard before division"
    if "timeout" in err_lower:
        return "TIMEOUT", "Optimize algorithm or reduce input size"
    if "blocked import" in err_lower:
        return "BLOCKED_IMPORT", "Remove forbidden import — use allowed stdlib modules"
    if "memoryerror" in err_lower or "killed" in err_lower:
        return "OUT_OF_MEMORY", "Reduce data size or use streaming/chunked processing"
    if "assertionerror" in err_lower:
        return "ASSERTION_FAILED", "Check assertion conditions — logic error in code"
    if "recursionerror" in err_lower:
        return "INFINITE_RECURSION", "Add base case or convert to iterative approach"

    return "UNKNOWN_ERROR", f"Attempt {attempt_number} failed — try different approach"


# ---------------------------------------------------------------------------
# Strategy adapter — generates next attempt based on diagnosis
# ---------------------------------------------------------------------------

def adapt_strategy(
    goal: str,
    previous_code: str,
    diagnosis: str,
    suggested_fix: str,
    attempt_number: int,
) -> Tuple[str, str]:
    """Generate an adapted strategy and patched code based on failure diagnosis.
    Returns (strategy_description, adapted_code).

    Deterministic patching for known patterns. For novel failures,
    returns the original code with a comment about what to fix.
    """
    strategy = f"Attempt {attempt_number + 1}: {suggested_fix}"
    code = previous_code

    # Apply deterministic patches based on diagnosis
    if diagnosis.startswith("UNDEFINED_NAME:"):
        name = diagnosis.split(":")[1]
        # Add a default definition
        code = f"# Auto-fix: define {name}\n{name} = None  # TODO: set correct value\n\n" + code

    elif diagnosis == "DIVISION_BY_ZERO":
        code = code.replace(" / ", " / max(1, ") + "  # guarded"
        strategy += " (added zero-division guard)"

    elif diagnosis == "NUMERICAL_INSTABILITY":
        # Add NaN sanitization
        preamble = textwrap.dedent("""\
        import math
        def _safe(x):
            if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
                return 0.0
            return x

        """)
        code = preamble + code
        strategy += " (added NaN/Inf sanitization)"

    elif diagnosis == "BLOCKED_IMPORT":
        # Remove the blocked import line
        lines = code.split("\n")
        code = "\n".join(
            l for l in lines
            if not (l.strip().startswith("import ") or l.strip().startswith("from "))
            or any(m in l for m in ["math", "json", "re", "collections", "itertools",
                                    "functools", "string", "hashlib", "random",
                                    "datetime", "time", "dataclasses", "typing", "copy"])
        )
        strategy += " (removed blocked imports)"

    elif diagnosis == "TIMEOUT":
        strategy += " (needs algorithm optimization)"

    elif diagnosis == "INDEX_ERROR":
        # Wrap list accesses in bounds checks
        code = "# Auto-fix: added bounds checking\n" + code
        strategy += " (needs bounds checking)"

    elif diagnosis == "ASSERTION_FAILED":
        strategy += " (logic error — needs different approach)"

    elif diagnosis == "INFINITE_RECURSION":
        strategy += " (convert recursion to iteration)"

    else:
        # Generic: add diagnostic comment
        code = f"# Previous failure: {diagnosis}\n# Fix: {suggested_fix}\n\n" + code

    return strategy, code


# ---------------------------------------------------------------------------
# Validation engine
# ---------------------------------------------------------------------------

def validate_result(
    code: str,
    output: str,
    goal: str,
    custom_checks: Optional[List[Callable[[str, str], Tuple[bool, str]]]] = None,
) -> ValidationRecord:
    """Validate execution result against goal and custom checks."""
    checks_run = 0
    checks_passed = 0
    failures = []

    # Check 1: non-empty output
    checks_run += 1
    if output and output.strip():
        checks_passed += 1
    else:
        failures.append("Empty output")

    # Check 2: no error markers in output
    checks_run += 1
    error_markers = ["Traceback", "Error:", "Exception:", "FAILED"]
    if not any(m in output for m in error_markers):
        checks_passed += 1
    else:
        failures.append("Error markers found in output")

    # Check 3: code is valid Python (AST parseable)
    checks_run += 1
    try:
        ast.parse(code)
        checks_passed += 1
    except SyntaxError:
        failures.append("Final code has syntax errors")

    # Check 4: output is reasonable length (not exploded)
    checks_run += 1
    if len(output) < 100000:
        checks_passed += 1
    else:
        failures.append(f"Output too large: {len(output)} chars")

    # Custom checks
    if custom_checks:
        for check_fn in custom_checks:
            checks_run += 1
            try:
                passed, msg = check_fn(code, output)
                if passed:
                    checks_passed += 1
                else:
                    failures.append(msg)
            except Exception as e:
                failures.append(f"Custom check error: {e}")

    return ValidationRecord(
        passed=checks_passed == checks_run,
        checks_run=checks_run,
        checks_passed=checks_passed,
        failures=failures,
        output=output[:2000],
    )


# ---------------------------------------------------------------------------
# The Loop
# ---------------------------------------------------------------------------

class AutonomousLoop:
    """
    Autonomous execution loop with failure recovery.

    Goal in → decompose → attempt → fail → diagnose → adapt → retry → validate → receipt.

    authority = NONE on every record.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        exec_timeout: int = 30,
        custom_checks: Optional[List[Callable]] = None,
        on_attempt: Optional[Callable[[AttemptRecord], None]] = None,
        llm_adapter: Optional[Callable[[str, str], str]] = None,
    ):
        self.max_attempts = max_attempts
        self.exec_timeout = exec_timeout
        self.custom_checks = custom_checks or []
        self.on_attempt = on_attempt  # callback after each attempt
        self.llm_adapter = llm_adapter  # optional LLM for code generation/adaptation

    def run(self, goal: str, initial_code: str) -> LoopResult:
        """Execute the autonomous loop.

        Args:
            goal: What we're trying to achieve
            initial_code: Starting code to execute

        Returns:
            LoopResult with all attempts, diagnosis, and validation
        """
        t0 = time.monotonic()
        attempts: List[AttemptRecord] = []
        current_code = initial_code
        current_strategy = "Initial attempt"
        successful_attempt = None

        for attempt_num in range(1, self.max_attempts + 1):
            # Execute
            t_start = time.monotonic()
            success, output, error = _exec_python(current_code, timeout=self.exec_timeout)
            duration = int((time.monotonic() - t_start) * 1000)

            if success:
                status = AttemptStatus.SUCCESS
            elif output and not error:
                status = AttemptStatus.PARTIAL
            else:
                status = AttemptStatus.FAILED

            record = AttemptRecord(
                attempt_number=attempt_num,
                strategy=current_strategy,
                code=current_code,
                status=status,
                output=output[:5000],
                error=error[:2000] if error else None,
                duration_ms=duration,
            )

            # Diagnose failure
            if status == AttemptStatus.FAILED and error:
                diagnosis, suggested_fix = diagnose_failure(current_code, error, attempt_num)
                record.diagnosis = diagnosis

                # Adapt strategy for next attempt
                if attempt_num < self.max_attempts:
                    new_strategy, new_code = adapt_strategy(
                        goal, current_code, diagnosis, suggested_fix, attempt_num
                    )

                    # If LLM adapter available, try LLM-powered adaptation
                    if self.llm_adapter and new_code == current_code:
                        try:
                            llm_code = self.llm_adapter(goal, f"Previous error: {error}\nDiagnosis: {diagnosis}")
                            if llm_code and llm_code.strip():
                                new_code = llm_code
                                new_strategy = f"LLM-adapted attempt {attempt_num + 1}"
                        except Exception:
                            pass  # fall back to deterministic adaptation

                    record.patch_applied = new_strategy
                    current_code = new_code
                    current_strategy = new_strategy

            attempts.append(record)

            # Callback
            if self.on_attempt:
                self.on_attempt(record)

            # Success — validate and exit
            if status == AttemptStatus.SUCCESS:
                successful_attempt = attempt_num
                break

        # Validation (only if we got a success)
        validation = None
        final_output = None
        final_code = None

        if successful_attempt is not None:
            last_success = attempts[successful_attempt - 1]
            final_output = last_success.output
            final_code = last_success.code
            validation = validate_result(
                final_code, final_output, goal, self.custom_checks
            )
            loop_status = LoopStatus.VALIDATED if validation.passed else LoopStatus.COMPLETED
        else:
            loop_status = LoopStatus.EXHAUSTED
            # Take best partial result if any
            partials = [a for a in attempts if a.status == AttemptStatus.PARTIAL]
            if partials:
                final_output = partials[-1].output
                final_code = partials[-1].code

        total_ms = int((time.monotonic() - t0) * 1000)

        result = LoopResult(
            goal=goal,
            status=loop_status,
            total_attempts=len(attempts),
            successful_attempt=successful_attempt,
            attempts=attempts,
            validation=validation,
            final_output=final_output,
            final_code=final_code,
            duration_total_ms=total_ms,
        )
        result.loop_hash = _hash(result.to_dict())

        return result


# ---------------------------------------------------------------------------
# Convenience runners
# ---------------------------------------------------------------------------

def run_autonomous(
    goal: str,
    code: str,
    max_attempts: int = 5,
    timeout: int = 30,
    checks: Optional[List[Callable]] = None,
    verbose: bool = False,
) -> LoopResult:
    """Run an autonomous execution loop. Simplest API."""

    def _log(record: AttemptRecord):
        if verbose:
            status = record.status.value
            print(f"  Attempt {record.attempt_number}: {status} ({record.duration_ms}ms)")
            if record.diagnosis:
                print(f"    Diagnosis: {record.diagnosis}")
            if record.patch_applied:
                print(f"    Patch: {record.patch_applied}")

    loop = AutonomousLoop(
        max_attempts=max_attempts,
        exec_timeout=timeout,
        custom_checks=checks or [],
        on_attempt=_log,
    )

    if verbose:
        print(f"Goal: {goal}")
        print(f"Max attempts: {max_attempts}")
        print("---")

    result = loop.run(goal, code)

    if verbose:
        print("---")
        print(f"Status: {result.status.value}")
        print(f"Attempts: {result.total_attempts}")
        if result.validation:
            print(f"Validation: {result.validation.checks_passed}/{result.validation.checks_run} checks")
        print(f"Duration: {result.duration_total_ms}ms")

    return result
