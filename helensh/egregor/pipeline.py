"""HELEN OS — Egregor Street Pipeline.

A governed, receipted, multi-agent coding pipeline (ChatDev-style superteam).

Pipeline phases:
  ARCHITECT → CODER → REVIEWER → TESTER → VALIDATOR → (feedback loop)

Every phase produces receipts. The whole session is immutable and replayable.
Authority: False on every receipt. Base state: never mutated.

Design constraints:
  - authority=False structurally enforced on all PhaseResult and receipts
  - feedback loop: REVIEWER reject → CODER retry (max N)
  - validation below threshold → CODER retry (max N)
  - OllamaError → graceful fallback at every phase
  - session hash is deterministic given same phase outputs
  - receipt chain: previous_hash links from EGREGOR_GENESIS
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical, canonical_hash
from helensh.agents.hal_reviewer import HalReviewer
from helensh.adapters.ollama import OllamaClient, OllamaError
from helensh.egregor.roles import (
    ROLES,
    MODEL_FALLBACK,
)


# ── Data Structures ──────────────────────────────────────────────────


@dataclass(frozen=True)
class SubTask:
    """A decomposed subtask from ARCHITECT phase."""

    id: int
    title: str
    description: str
    target: str
    dependencies: Tuple[int, ...] = ()


@dataclass(frozen=True)
class PhaseResult:
    """Result of one phase execution.

    authority is always False — structural invariant.
    """

    phase: str
    role: str
    subtask_id: Optional[int]
    output: dict
    verdict: str  # APPROVE, REJECT, REQUEST_CHANGES, N/A
    confidence: float
    receipt_hash: str
    authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "authority", False)


@dataclass(frozen=True)
class CodeUnit:
    """A code artifact produced by the pipeline."""

    subtask_id: int
    target: str
    code: str
    tests: Optional[str]
    validation_score: float
    approved: bool
    retries: int


@dataclass(frozen=True)
class EgregorSession:
    """Complete pipeline session — immutable, receipted, replayable."""

    task: str
    subtasks: Tuple[SubTask, ...]
    phase_results: Tuple[PhaseResult, ...]
    code_units: Tuple[CodeUnit, ...]
    receipt_chain: Tuple[dict, ...]
    session_hash: str
    total_phases: int
    approved_count: int
    rejected_count: int
    validation_mean: float


# ── Receipt Generation ───────────────────────────────────────────────


def _make_phase_receipt(
    phase: str,
    role: str,
    subtask_id: Optional[int],
    output: dict,
    verdict: str,
    previous_hash: str,
) -> dict:
    """Create a receipt for a pipeline phase."""
    receipt = {
        "type": "EGREGOR_PHASE",
        "phase": phase,
        "role": role,
        "subtask_id": subtask_id,
        "output_hash": canonical_hash(output),
        "verdict": verdict,
        "authority": False,
        "previous_hash": previous_hash,
        "timestamp_ns": time.monotonic_ns(),
    }
    receipt["hash"] = canonical_hash(receipt)
    return receipt


# ── JSON Extraction ──────────────────────────────────────────────────


def _extract_json_safe(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from model output."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ── Helpers ──────────────────────────────────────────────────────────


def _parse_subtasks(parsed: dict) -> List[SubTask]:
    """Parse ARCHITECT output into SubTask list."""
    raw_tasks = parsed.get("subtasks", [])
    if not isinstance(raw_tasks, list):
        return []
    subtasks: List[SubTask] = []
    for i, raw in enumerate(raw_tasks[:10]):  # max 10 subtasks
        if not isinstance(raw, dict):
            continue
        deps = raw.get("dependencies", ())
        if isinstance(deps, list):
            deps = tuple(deps)
        elif not isinstance(deps, tuple):
            deps = ()
        subtasks.append(SubTask(
            id=raw.get("id", i + 1),
            title=str(raw.get("title", f"subtask-{i + 1}")),
            description=str(raw.get("description", "")),
            target=str(raw.get("target", "module.py")),
            dependencies=deps,
        ))
    return subtasks


def _subtask_to_dict(st: SubTask) -> dict:
    """Convert SubTask to serializable dict."""
    return {
        "id": st.id,
        "title": st.title,
        "description": st.description,
        "target": st.target,
        "dependencies": list(st.dependencies),
    }


def _extract_code(output: dict) -> Optional[str]:
    """Extract code string from a CODER/TESTER phase output."""
    payload = output.get("payload", {})
    if isinstance(payload, dict):
        code = payload.get("code")
        if code and isinstance(code, str):
            return code.strip()
    # Try top-level 'code' key
    code = output.get("code")
    if code and isinstance(code, str):
        return code.strip()
    return None


# ── EgregorStreet Pipeline ───────────────────────────────────────────


class EgregorStreet:
    """Governed multi-agent coding superteam.

    Pipeline: ARCHITECT → CODER → REVIEWER → TESTER → VALIDATOR
    All phases receipted. Authority always False. Base state never mutated.

    Feedback loops:
      - REVIEWER reject → CODER retry (max_retries)
      - Validation below threshold → CODER retry (max_retries)

    Usage:
        egregor = EgregorStreet(her, hal)
        session = egregor.run("build a REST API for task management")
        assert egregor.verify_session(session)
    """

    def __init__(
        self,
        hal: HalReviewer,
        client: Optional[OllamaClient] = None,
        max_retries: int = 3,
        validation_threshold: float = 0.6,
    ) -> None:
        self.hal = hal
        self.client = client or hal.client
        self.max_retries = max_retries
        self.validation_threshold = validation_threshold

    def run(self, task: str, state: Optional[dict] = None) -> EgregorSession:
        """Execute the full Egregor Street pipeline.

        Returns an immutable EgregorSession with full receipt chain.
        """
        if state is None:
            from helensh.kernel import init_session
            state = init_session(session_id="egregor-run")

        receipts: List[dict] = []
        phase_results: List[PhaseResult] = []
        code_units: List[CodeUnit] = []
        previous_hash = "EGREGOR_GENESIS"

        # ── Phase 1: ARCHITECT ────────────────────────────────────
        subtasks, arch_result, arch_receipt = self._phase_architect(
            task, state, previous_hash,
        )
        receipts.append(arch_receipt)
        phase_results.append(arch_result)
        previous_hash = arch_receipt["hash"]

        # ── Phase 2-5: Per-subtask pipeline ───────────────────────
        for subtask in subtasks:
            unit, sub_results, sub_receipts = self._subtask_pipeline(
                subtask, task, state, previous_hash,
            )
            code_units.append(unit)
            phase_results.extend(sub_results)
            receipts.extend(sub_receipts)
            if sub_receipts:
                previous_hash = sub_receipts[-1]["hash"]

        # ── Build session ─────────────────────────────────────────
        approved = sum(1 for u in code_units if u.approved)
        rejected = len(code_units) - approved
        scores = [u.validation_score for u in code_units]
        mean_score = sum(scores) / len(scores) if scores else 0.0

        session_data = {
            "task": task,
            "subtask_count": len(subtasks),
            "approved": approved,
            "rejected": rejected,
            "mean_score": round(mean_score, 6),
            "receipt_count": len(receipts),
        }

        return EgregorSession(
            task=task,
            subtasks=tuple(subtasks),
            phase_results=tuple(phase_results),
            code_units=tuple(code_units),
            receipt_chain=tuple(receipts),
            session_hash=canonical_hash(session_data),
            total_phases=len(phase_results),
            approved_count=approved,
            rejected_count=rejected,
            validation_mean=mean_score,
        )

    # ── Phase implementations ────────────────────────────────────────

    def _phase_architect(
        self,
        task: str,
        state: dict,
        previous_hash: str,
    ) -> Tuple[List[SubTask], PhaseResult, dict]:
        """ARCHITECT phase: decompose task into subtasks."""
        role_config = ROLES["architect"]
        prompt = f"Decompose this coding task into subtasks:\n\n{task}"

        raw = self._call_model(
            role_config.model,
            role_config.fallback_model,
            prompt,
            role_config.system_prompt,
            role_config.temperature,
        )

        parsed = _extract_json_safe(raw) or {}
        subtasks = _parse_subtasks(parsed)

        # If no subtasks parsed, create a single default
        if not subtasks:
            subtasks = [SubTask(
                id=1,
                title=task[:60],
                description=task,
                target="main.py",
            )]

        output = {"subtasks": [_subtask_to_dict(s) for s in subtasks]}
        receipt = _make_phase_receipt(
            "architect", "architect", None, output, "N/A", previous_hash,
        )

        result = PhaseResult(
            phase="architect",
            role="architect",
            subtask_id=None,
            output=output,
            verdict="N/A",
            confidence=parsed.get("confidence", 0.5),
            receipt_hash=receipt["hash"],
        )

        return subtasks, result, receipt

    def _subtask_pipeline(
        self,
        subtask: SubTask,
        task_context: str,
        state: dict,
        previous_hash: str,
    ) -> Tuple[CodeUnit, List[PhaseResult], List[dict]]:
        """Run CODER → REVIEWER → TESTER → VALIDATOR for one subtask."""
        results: List[PhaseResult] = []
        receipts: List[dict] = []
        feedback: Optional[str] = None
        best_code: str = ""
        best_tests: Optional[str] = None
        best_score: float = 0.0
        retries_used: int = 0

        for attempt in range(self.max_retries + 1):
            retries_used = attempt

            # ── CODER ─────────────────────────────────────────────
            code_output = self._phase_code(subtask, task_context, feedback)
            code_receipt = _make_phase_receipt(
                "coder", "coder", subtask.id, code_output,
                "N/A", previous_hash,
            )
            receipts.append(code_receipt)
            previous_hash = code_receipt["hash"]

            code_text = _extract_code(code_output)
            results.append(PhaseResult(
                phase="coder",
                role="coder",
                subtask_id=subtask.id,
                output=code_output,
                verdict="N/A",
                confidence=code_output.get("confidence", 0.5),
                receipt_hash=code_receipt["hash"],
            ))

            if not code_text:
                feedback = "No code was produced. Please write complete, runnable code."
                continue

            # ── REVIEWER ──────────────────────────────────────────
            review_proposal = {
                "action": "write_code",
                "target": subtask.target,
                "payload": {
                    "code": code_text,
                    "description": subtask.description,
                },
                "confidence": code_output.get("confidence", 0.5),
                "authority": False,
            }
            review = self.hal.review(review_proposal, state)
            review_verdict = review.get("verdict", "REJECT")

            review_receipt = _make_phase_receipt(
                "reviewer", "reviewer", subtask.id, review,
                review_verdict, previous_hash,
            )
            receipts.append(review_receipt)
            previous_hash = review_receipt["hash"]

            results.append(PhaseResult(
                phase="reviewer",
                role="reviewer",
                subtask_id=subtask.id,
                output=review,
                verdict=review_verdict,
                confidence=review.get("confidence", 0.5),
                receipt_hash=review_receipt["hash"],
            ))

            if review_verdict in ("REJECT", "REQUEST_CHANGES"):
                rationale = review.get("rationale", "No rationale")
                issues = review.get("issues", [])
                feedback = (
                    f"REJECTED by reviewer: {rationale}\n"
                    f"Issues: {', '.join(str(i) for i in issues)}"
                )
                continue

            # ── Past reviewer — code approved by HAL ──────────────

            # ── TESTER ────────────────────────────────────────────
            test_output = self._phase_test(code_text, subtask)
            test_receipt = _make_phase_receipt(
                "tester", "tester", subtask.id, test_output,
                "N/A", previous_hash,
            )
            receipts.append(test_receipt)
            previous_hash = test_receipt["hash"]

            test_text = _extract_code(test_output)
            results.append(PhaseResult(
                phase="tester",
                role="tester",
                subtask_id=subtask.id,
                output=test_output,
                verdict="N/A",
                confidence=test_output.get("confidence", 0.5),
                receipt_hash=test_receipt["hash"],
            ))

            # ── VALIDATOR ─────────────────────────────────────────
            val_score = self._phase_validate(code_text, test_text)
            val_verdict = (
                "APPROVE"
                if val_score >= self.validation_threshold
                else "REJECT"
            )

            val_output = {
                "combined_score": val_score,
                "threshold": self.validation_threshold,
                "code_length": len(code_text),
            }
            val_receipt = _make_phase_receipt(
                "validator", "validator", subtask.id, val_output,
                val_verdict, previous_hash,
            )
            receipts.append(val_receipt)
            previous_hash = val_receipt["hash"]

            results.append(PhaseResult(
                phase="validator",
                role="validator",
                subtask_id=subtask.id,
                output=val_output,
                verdict=val_verdict,
                confidence=val_score,
                receipt_hash=val_receipt["hash"],
            ))

            best_code = code_text
            best_tests = test_text
            best_score = val_score

            if val_score >= self.validation_threshold:
                break  # fully approved

            # Validation too low — retry with feedback
            feedback = (
                f"Validation score {val_score:.2f} below threshold "
                f"{self.validation_threshold}. Fix the code."
            )

        return (
            CodeUnit(
                subtask_id=subtask.id,
                target=subtask.target,
                code=best_code,
                tests=best_tests,
                validation_score=best_score,
                approved=best_score >= self.validation_threshold,
                retries=retries_used,
            ),
            results,
            receipts,
        )

    # ── Model call helpers ───────────────────────────────────────────

    def _call_model(
        self,
        model: str,
        fallback: str,
        prompt: str,
        system: str,
        temperature: float,
    ) -> str:
        """Call Ollama model with fallback. Returns raw text."""
        try:
            return self.client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                system=system,
                temperature=temperature,
            )
        except OllamaError:
            pass
        try:
            return self.client.chat(
                model=fallback,
                messages=[{"role": "user", "content": prompt}],
                system=system,
                temperature=temperature,
            )
        except OllamaError:
            return ""

    def _phase_code(
        self,
        subtask: SubTask,
        task_context: str,
        feedback: Optional[str],
    ) -> dict:
        """CODER phase: generate code for a subtask."""
        role_config = ROLES["coder"]
        prompt = (
            f"Task context: {task_context}\n\n"
            f"Subtask: {subtask.title}\n"
            f"Description: {subtask.description}\n"
            f"Target file: {subtask.target}\n"
        )
        if feedback:
            prompt += f"\nFEEDBACK FROM PREVIOUS ATTEMPT:\n{feedback}\n"
        prompt += "\nWrite complete, runnable Python code."

        raw = self._call_model(
            role_config.model,
            role_config.fallback_model,
            prompt,
            role_config.system_prompt,
            role_config.temperature,
        )

        if not raw:
            return {
                "action": "write_code",
                "target": subtask.target,
                "payload": {"code": None, "description": "OllamaError"},
                "confidence": 0.0,
                "authority": False,
                "fallback": True,
            }

        parsed = _extract_json_safe(raw) or {
            "action": "write_code",
            "target": subtask.target,
            "payload": {"code": raw, "description": "Raw model output"},
            "confidence": 0.3,
        }
        parsed["authority"] = False
        return parsed

    def _phase_test(self, code: str, subtask: SubTask) -> dict:
        """TESTER phase: generate tests for approved code."""
        role_config = ROLES["tester"]
        prompt = (
            f"Write pytest tests for this code:\n\n"
            f"Subtask: {subtask.title}\n"
            f"Target: {subtask.target}\n\n"
            f"```python\n{code}\n```\n\n"
            f"Write complete, runnable pytest tests."
        )

        raw = self._call_model(
            role_config.model,
            role_config.fallback_model,
            prompt,
            role_config.system_prompt,
            role_config.temperature,
        )

        if not raw:
            return {
                "action": "write_tests",
                "target": f"test_{subtask.target}",
                "payload": {"code": None, "description": "OllamaError"},
                "confidence": 0.0,
                "authority": False,
                "fallback": True,
            }

        parsed = _extract_json_safe(raw) or {
            "action": "write_tests",
            "target": f"test_{subtask.target}",
            "payload": {"code": raw, "description": "Raw model output"},
            "confidence": 0.3,
        }
        parsed["authority"] = False
        return parsed

    def _phase_validate(
        self,
        code: str,
        tests: Optional[str],
    ) -> float:
        """VALIDATOR phase: score code with AST + exec + tests."""
        try:
            from helensh.sandbox.validate import validate
            result = validate(code, test_content=tests)
            return result.combined_score
        except Exception:
            # If validate is unavailable, use AST-only check
            try:
                import ast
                ast.parse(code)
                return 0.3  # valid syntax only
            except SyntaxError:
                return 0.0

    # ── Session verification ─────────────────────────────────────────

    def verify_session(self, session: EgregorSession) -> bool:
        """Verify receipt chain integrity and authority invariant."""
        chain = session.receipt_chain
        if not chain:
            return True

        # Verify genesis link
        if chain[0]["previous_hash"] != "EGREGOR_GENESIS":
            return False

        # Verify chain links
        for i in range(1, len(chain)):
            if chain[i]["previous_hash"] != chain[i - 1]["hash"]:
                return False

        # Verify authority invariant
        for receipt in chain:
            if receipt.get("authority") is not False:
                return False

        return True


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "EgregorStreet",
    "EgregorSession",
    "SubTask",
    "PhaseResult",
    "CodeUnit",
    "_make_phase_receipt",
    "_parse_subtasks",
    "_extract_code",
    "_extract_json_safe",
]
