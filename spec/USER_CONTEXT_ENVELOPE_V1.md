# USER_CONTEXT_ENVELOPE_V1

Status: DRAFT_ADMISSIBLE  
Authority: NON_SOVEREIGN  
Scope: `/init` context assembly

## Purpose

Carry session-local user context into `/init` without mutating durable identity memory, kernel identity, ledger facts, or sovereign truth.

This envelope exists to prevent implicit promotion drift, especially the unsafe jump from account metadata to identity authority.

```text
account label says Jean-Marie Tassy
-> advisory identity hint
-> ask / observe / confirm
-> promote only with receipt
```

## Boundary

`USER_CONTEXT_ENVELOPE_V1` is a boundary object between session context and the HELEN kernel.

It may guide interface behavior. It may not rewrite identity.

Locked doctrine:

```text
Personalization may guide the interface.
Confirmation governs identity.
```

## Authority

```yaml
status: DRAFT_ADMISSIBLE
authority: NON_SOVEREIGN
scope: /init context assembly
promotion_policy: confirmation_or_receipt_required
```

## Fields

| Field | Type | Purpose |
| --- | --- | --- |
| `local_time` | string or null | Session-local timestamp, preferably ISO-8601. |
| `timezone` | string or null | IANA timezone or explicit timezone label. |
| `account_label` | string or null | Account-display label, treated only as an identity hint. |
| `preferred_name` | string or null | User-confirmed or candidate preferred name. |
| `preferred_name_status` | enum | Status of the preferred-name value. |
| `identity_hint` | string or null | Non-sovereign identity clue. |
| `language_preference` | string or null | Interface language preference. |
| `provenance` | object | Source, observed_at, and evidence reference for each populated field. |
| `confidence` | object | Per-field confidence values in `[0, 1]`. |
| `uncertainty_flags` | array | Explicit unresolved context uncertainties. |
| `promotion_status` | enum | Current promotion level for durable use. |

## Status enums

### `preferred_name_status`

```text
UNKNOWN
CANDIDATE
OBSERVED
CONFIRMED
DURABLE
```

### `promotion_status`

```text
ADVISORY
OBSERVED
CONFIRMED
DURABLE
```

## Rules

1. The envelope may shape tone, timing, language, and coordination.
2. The envelope may not mutate kernel identity.
3. Account labels are hints, not sovereign facts.
4. Unknown preferences must remain explicit.
5. Promotion requires confirmation or receipt.
6. Any durable effect must preserve provenance and confidence.
7. `CONFIRMED` or higher is required before a field may influence durable identity memory.
8. Session-local context may be discarded without loss of kernel truth.

## Forbidden transitions

```text
account_label -> identity truth
session_context -> ledger fact
inferred preference -> confirmed preference
repeated mention -> canon without review
identity_hint -> authority
```

## Promotion path

```text
ADVISORY -> OBSERVED -> CONFIRMED -> DURABLE
```

Only `CONFIRMED` or `DURABLE` fields may influence durable memory.

Promotion requires at least one of:

- explicit user confirmation;
- durable receipt from a trusted identity subsystem;
- manual review under kernel governance.

Repeated observation alone is insufficient.

## Example

```yaml
local_time:
  value: "2026-05-05T02:07:00+02:00"
  timezone: "Europe/Paris"
  status: "EXECUTION_CONTEXT"
  authority: "NON_SOVEREIGN"

account_label:
  value: "Jean-Marie Tassy"
  status: "IDENTITY_HINT"
  authority: "NON_SOVEREIGN"

preferred_name:
  value: null
  status: "UNKNOWN"
  required_action: "ASK_OR_WAIT_FOR_CONFIRMATION"
```

## Main risk

The envelope itself is safe. The risk is promotion drift:

```text
context hint becomes memory
memory becomes identity
identity becomes authority
authority bypasses reducer
```

Therefore the gate is explicit:

```text
No context field becomes durable identity without confirmation.
```

## Architectural placement

```text
User / Runtime Signals
        |
        v
USER_CONTEXT_ENVELOPE_V1  --NON_SOVEREIGN-->  /init Context Assembly
        |                                          |
        |                                          v
        |                                  Interface adaptation
        |
        x  Kernel identity mutation without confirmation
```

## Acceptance condition

An implementation conforms to this spec when:

- account labels remain identity hints;
- unknown preferred names remain explicitly unknown;
- durable promotion is blocked unless status is `CONFIRMED` or `DURABLE`;
- provenance and confidence are preserved for every durable effect;
- forbidden transitions are rejected by tests or reducer policy.
