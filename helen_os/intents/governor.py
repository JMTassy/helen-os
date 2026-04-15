"""
HELEN OS Intent Governor — validates intent envelopes before execution.

Checks:
1. Known intent_type
2. Payload schema valid (required fields present)
3. No sovereign fields
4. Confidence threshold
5. Authority must be NONE

Returns: (allowed, rejection_reason)
"""

from typing import Dict, Any, Optional, Tuple

from helen_os.intents.schemas import (
    ALL_INTENT_TYPES, INTENT_REGISTRY, IntentEnvelope, validate_payload,
)


MIN_CONFIDENCE = 0.1  # very permissive; tighten per use case


def govern_intent(envelope: IntentEnvelope) -> Tuple[bool, Optional[str]]:
    """
    Governor gate for intent execution.

    Returns:
        (True, None) — allowed
        (False, reason) — rejected with reason string
    """
    # Gate 1: known intent type
    if envelope.intent_type not in ALL_INTENT_TYPES:
        return False, f"Unknown intent type: {envelope.intent_type}"

    # Gate 2: authority must be NONE
    if envelope.authority != "NONE":
        return False, f"Authority must be NONE, got: {envelope.authority}"

    # Gate 3: confidence threshold
    if envelope.confidence < MIN_CONFIDENCE:
        return False, f"Confidence {envelope.confidence} below threshold {MIN_CONFIDENCE}"

    # Gate 4: payload schema validation
    valid, errors = validate_payload(envelope.intent_type, envelope.payload)
    if not valid:
        return False, f"Payload validation failed: {'; '.join(errors)}"

    # Gate 5: no sovereign fields in payload
    forbidden_keys = {"authority", "sovereign", "admin", "root", "sudo"}
    payload_keys = set(envelope.payload.keys())
    leaked = payload_keys & forbidden_keys
    if leaked:
        return False, f"Sovereign fields in payload: {leaked}"

    return True, None
