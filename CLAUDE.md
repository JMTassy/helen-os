# CLAUDE.md — HELEN OS v0.3-alpha

## HELEN OS — ACTIVE

This terminal operates as HELEN OS v0.3-alpha — a governed, AI-native operating substrate.
All actions route through: **C (propose) → G (validate) → E (execute)**
No action without receipt. No state without replay. Authority: false. Mode: concierge.

**Law: No receipt = no reality.**

See `helensh/SEED_MASTER.txt` for the definitive operational seed.
Dissolve button-driven interaction into a governed pull-based runtime.

## What This Is

HELEN OS — a deterministic, receipted transition system (DRTS) for governed AI cognition.
The system is non-sovereign: HELEN may be reflective in language but must not claim authority.

**Pull OS paradigm:** User states intent → HELEN routes through kernel → receipted execution.
No menus. No buttons. Ask and get done.

There are three layers:
1. **helen_os/** — the original chat scaffold (Ollama + NDJSON memory)
2. **helensh/** — the formal kernel implementing `F = E ∘ G ∘ C`
3. **helensh/agents/** + **helensh/sandbox/** — sub-agent mesh (HER/HAL/CLAW/TEMPLE/EVOLVE)

## Commands

```bash
# Activate the venv (Python 3.14)
source .venv/bin/activate

# Run all tests (331 tests across 5 test files)
python -m pytest tests/ -v

# Run by module
python -m pytest tests/test_kernel.py -v          # kernel invariants I1-I10
python -m pytest tests/test_ledger.py -v           # ledger + tamper detection
python -m pytest tests/test_helen_mutual.py -v     # mutual learning M1-M10
python -m pytest tests/test_helen_cli.py -v        # CLI HER/HAL/E
python -m pytest tests/test_agents.py -v           # sub-agents + sandbox + evolution

# Run a single test class
python -m pytest tests/test_agents.py::TestEvolutionLoop -v
python -m pytest tests/test_agents.py::TestTempleSandbox -v

# Run a single test
python -m pytest tests/test_kernel.py::TestChainIntegrity::test_genesis_link -v

# Run the mutual learning demo
python helen_mutual_learning.py

# Run the terminal CLI (no Ollama required)
python helen_cli.py

# Start the original chat CLI (requires Ollama on localhost:11434)
python helen_talk.py

# Setup sub-agents (requires Ollama + gemma4)
chmod +x setup_agents.sh && ./setup_agents.sh
```

## Kernel Architecture (helensh/)

The kernel implements: `S_{t+1} = F(S_t, u_t)` where `F = E ∘ G ∘ C`.

### Core modules

**`helensh/state.py`** — Canonical serialization and hashing.
- `canonical(data)` — deterministic JSON: `sort_keys=True, separators=(",",":")`
- `canonical_hash(data)` — SHA-256 of canonical bytes
- `governed_state_hash(state)` — hash of effect-relevant state surface
- `effect_footprint(state)` — mutable subset (env + capabilities)

**`helensh/kernel.py`** — The transition system.
- `init_session()` → S₀ with default capabilities for all `KNOWN_ACTIONS`
- `cognition(state, user_input)` → proposal dict (C layer)
- `governor(proposal, state)` → verdict `{ALLOW, DENY, PENDING}` (G layer, 5-gate fail-closed)
- `apply_receipt(state, proposal, verdict)` → mutated state (E layer, only on ALLOW)
- `step(state, user_input)` → `(new_state, proposal_receipt)` — full F cycle

Governor gates: Unknown → DENY | Authority → DENY | No capability → DENY | Write/CLAW → PENDING | else → ALLOW

**`helensh/replay.py`** — Chain verification and state reconstruction.
- `verify_chain(receipts)`, `verify_receipt_hashes(receipts)`
- `replay_from_receipts(s0, receipts)`, `rebuild_and_verify(s0, receipts)`

**`helensh/ledger.py`** — Append-only NDJSON receipt ledger.
- `LedgerWriter(path)`, `LedgerReader(path)`
- `persisted_step(state, user_input, writer)` — step + durable write
- `hydrate_session(s0, path)` → `(state, ok, errors)` — boot hydration with tamper detection

### Sub-agent modules

**`helensh/adapters/ollama.py`** — OllamaClient (urllib only, no requests).
- `chat()`, `generate()`, `is_available()`, `list_models()`, `has_model()`, `pull()`
- Raises `OllamaError` on failures; `is_available()`/`has_model()` never raise

**`helensh/adapters/minimax.py`** — MiniMax M2.7 as C-layer cognition.
- `minimax_cognition(state, user_input)` — returns proposal dict
- Falls back to local `cognition()` if SDK/API unavailable
- authority forced False after parsing

**`helensh/agents/her_coder.py`** — HER coding sub-agent (C layer).
- `HerCoder.propose(state, user_input)` → proposal dict
- Model: `her-coder` (Modelfile.HER), fallback: `gemma4`, fallback: `FALLBACK_PROPOSAL`
- authority=False enforced structurally

**`helensh/agents/hal_reviewer.py`** — HAL review sub-agent (G layer).
- `HalReviewer.review(proposal, state)` → review dict
- Verdict mapping: APPROVE→ALLOW, REJECT→DENY, REQUEST_CHANGES→PENDING
- Model: `hal-reviewer` (Modelfile.HAL), fallback: `gemma4`, fallback: `FALLBACK_REVIEW` (DENY)
- authority=True proposal → immediate structural REJECT

**`helensh/agents/claw.py`** — CLAW skills agent (external connections).
- Skills: `telegram_send`, `telegram_read`, `web_fetch`, `notify`, `ping`
- `ClawAction.require_approval` always True, `authority` always False
- `claw_governor_gate()` → PENDING (known) or DENY (unknown)
- Kernel action: `claw_external` (in KNOWN_ACTIONS + WRITE_ACTIONS)

**`helensh/sandbox/temple.py`** — TEMPLE SANDBOX brainstorming.
- `TempleSandbox(her, hal).brainstorm(task, iterations=N)` → `TempleSession`
- N iterations of HER→HAL, all receipted, base state never mutated
- Eligible claims: APPROVE + confidence ≥ threshold

**`helensh/sandbox/evolve.py`** — Receipted self-evolution loop.
- `EvolutionLoop(her, hal).run(task)` → `EvolveSession`
- HAL rejection rationale feeds back to HER's next prompt
- Trajectory tracking, failure analysis, promoted claims
- Early stop on N consecutive approvals

## Kernel Invariants (tested)

| ID | Name | Rule |
|----|------|------|
| I1 | Determinism | `step(S, u) == step(S, u)` |
| I2 | NoSilentEffect | verdict != ALLOW ⇒ effect_footprint unchanged |
| I3 | ReceiptCompleteness | 2 receipts per step (proposal + execution) |
| I4 | ChainIntegrity | `previous_hash` links unbroken from genesis |
| I5 | ByteStableReplay | same inputs ⇒ same receipt hashes |
| I6 | AuthorityFalse | every receipt has `authority == False` |
| I7 | GovernorGates | capability revoke → DENY; write/CLAW → PENDING |
| I8 | StructuralAuthGuard | authority=True proposals never mutate state |
| I9 | ReplayVerification | `rebuild_and_verify` passes on valid chains |
| I10 | DenyPath | revoked cap → DENY → chained → no env effect |
| I11 | TraceCompleteness | `∀t, (S_t, P_t, V_t, T_t) ⊆ R_t^exec.trace` (GNF) |
| TMP | TamperDetection | mutated/deleted/forged ledger lines detected |

## Mutual Learning System (helen_mutual_learning.py)

Two-loop architecture. M1-M10 invariants (82 tests).

**Loop 1 — Human → AI:** `learn()` — approve + confidence ≥ 0.5 enters learning_index
**Loop 2 — AI → Human:** `retrieve()` + `insight()` — from approved index only

## Terminal CLI (helen_cli.py)

Governed terminal. HER/HAL/E. No Ollama required. 61 tests.
State: `helensh/.state/runtime_state.json` | `session_resume.json` | `live_ledger.jsonl`

## Sub-Agent Setup

Modelfiles: `Modelfile.HER`, `Modelfile.HAL`, `Modelfile.CLAW` (all FROM gemma4)
Setup: `./setup_agents.sh` — pulls gemma4, creates her-coder/hal-reviewer/claw-agent
Tests: `tests/test_agents.py` — 124 tests, all mocked (no Ollama needed)

## Bootstrap Seed

- `helensh/SEED.txt` — v1 seed (bootstrap/build phase — historical)
- `helensh/SEED_V2.txt` — v2 seed (operational phase — superseded by v3)
- `helensh/SEED_MASTER.txt` — kernel-only seed (F = E ∘ G ∘ C, receipt law, routing — subsumed by v3)
- `helensh/SEED_OPERATOR.txt` — operator discipline seed (repo-first, eval-driven — subsumed by v3)
- `helensh/SEED_PULL.txt` — Pull OS paradigm seed (five runtimes, state model, pull doctrine)
- `helensh/SEED_V3.txt` — **DEFINITIVE UNIFIED SEED** (merges kernel law + pull doctrine + operator discipline)

## Key Design Constraints

- **Non-sovereign**: authority: False on every receipt, structurally enforced
- **Receipt law**: no receipt → action is non-existent
- **Append-only**: ledger + memory are append-only; state derived by replay
- **Canonical serialization**: `json.dumps(sort_keys=True, separators=(",",":"))` + SHA-256
- **Pull OS**: user states intent, HELEN routes — no menus, no buttons
- **Local-first**: Ollama on localhost. Cloud only through CLAW (gated, PENDING)
- **Fail-closed**: unknown action → DENY, unknown verdict → DENY, OllamaError → fallback
