"""HELEN OS — Street Exit Gate.

HAL-class verification at street boundary.
Nothing leaves a street without gate approval.

Checks:
    1. Schema validity    — artifact conforms to expected shape
    2. Mandate compliance — output is within street domain
    3. Forbidden actions  — no forbidden actions in output
    4. Receipt presence   — provenance chain required
    5. Authority check    — non-sovereign invariant
    6. Unresolved items   — no open obligations

Output: PASS | WARN | BLOCK
Not 'good'. Not 'promising'. Not 'beautiful'. Verdicts only.
"""
from __future__ import annotations

from typing import Any, Dict, List

from helensh.state import canonical_hash
from helensh.egregor.street_schema import (
    StreetCharter,
    StreetGateResult,
    StreetLedgerEntry,
)


class StreetGate:
    """HAL-class exit gate for a street.

    Runs deterministic checks on artifacts before they leave the street.
    Gate never approves its own street's output — it only validates.
    """

    def __init__(self, charter: StreetCharter) -> None:
        self.charter = charter

    def check(
        self,
        artifact: Dict[str, Any],
        receipts: List[str],
        ledger_entries: List[StreetLedgerEntry],
    ) -> StreetGateResult:
        """Run all gate checks. Returns verdict + reasons."""
        reasons: List[str] = []
        fixes: List[str] = []
        verdict = "PASS"

        def _escalate(new_verdict: str) -> None:
            nonlocal verdict
            # BLOCK > WARN > PASS (monotonic escalation)
            severity = {"PASS": 0, "WARN": 1, "BLOCK": 2}
            if severity.get(new_verdict, 0) > severity.get(verdict, 0):
                verdict = new_verdict

        # ── 1. Schema validity ──
        if not isinstance(artifact, dict):
            reasons.append("artifact is not a dict")
            _escalate("BLOCK")
        else:
            if not artifact.get("type"):
                reasons.append("artifact missing 'type' field")
                _escalate("WARN")

        # ── 2. Mandate compliance ──
        art_domain = artifact.get("domain", "") if isinstance(artifact, dict) else ""
        if art_domain and self.charter.allowed_domains:
            if art_domain not in self.charter.allowed_domains:
                reasons.append(
                    f"domain '{art_domain}' not in allowed: "
                    f"{list(self.charter.allowed_domains)}"
                )
                _escalate("BLOCK")

        # ── 3. Forbidden action check ──
        art_action = artifact.get("action", "") if isinstance(artifact, dict) else ""
        if art_action and art_action in self.charter.forbidden_actions:
            reasons.append(f"forbidden action: '{art_action}'")
            _escalate("BLOCK")

        # ── 4. Receipt presence ──
        if not receipts:
            reasons.append("no receipts — cannot verify provenance")
            _escalate("BLOCK")
            fixes.append("attach at least one receipt hash")

        # ── 5. Authority check (non-sovereign invariant) ──
        if isinstance(artifact, dict) and artifact.get("authority", False):
            reasons.append("artifact claims authority=True (non-sovereign violation)")
            _escalate("BLOCK")

        # ── 6. Unresolved obligations ──
        if isinstance(artifact, dict):
            obligations = artifact.get("obligations", [])
            if isinstance(obligations, list):
                unresolved = [
                    o for o in obligations
                    if isinstance(o, dict) and o.get("status") != "resolved"
                ]
                if unresolved:
                    reasons.append(f"{len(unresolved)} unresolved obligation(s)")
                    _escalate("WARN")
                    fixes.extend(
                        f"resolve: {o.get('id', '?')}" for o in unresolved
                    )

        # ── Compute replay hash ──
        replay_hash = canonical_hash({
            "charter_id": self.charter.street_id,
            "artifact_type": artifact.get("type", "") if isinstance(artifact, dict) else "",
            "receipt_count": len(receipts),
        })

        return StreetGateResult(
            verdict=verdict,
            reasons=tuple(reasons),
            required_fixes=tuple(fixes),
            receipts=tuple(receipts),
            replay_hash=replay_hash,
        )


__all__ = ["StreetGate"]
