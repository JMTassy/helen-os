#!/usr/bin/env bash
# HELEN OS — Sub-Agent Setup Script
# Checks Ollama, pulls gemma4, creates named HER/HAL/CLAW models.
#
# Usage:
#   chmod +x setup_agents.sh
#   ./setup_agents.sh

set -euo pipefail

OLLAMA_URL="http://localhost:11434"
BASE_MODEL="gemma4"

log()  { echo "[HELEN] $*"; }
warn() { echo "[HELEN][WARN] $*" >&2; }
fail() { echo "[HELEN][FAIL] $*" >&2; exit 1; }

# ── 1. Check Ollama is running ────────────────────────────────────────
log "Checking Ollama at $OLLAMA_URL ..."
if ! curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
    fail "Ollama is not reachable at $OLLAMA_URL. Start it with: ollama serve"
fi
log "Ollama is up."

# ── 2. Pull base model ────────────────────────────────────────────────
log "Pulling base model: $BASE_MODEL ..."
if ollama list 2>/dev/null | grep -q "^${BASE_MODEL}"; then
    log "$BASE_MODEL already present — skipping pull."
else
    ollama pull "$BASE_MODEL" || fail "Failed to pull $BASE_MODEL"
fi

# ── 3. Create named models from Modelfiles ────────────────────────────
create_model() {
    local name="$1"
    local modelfile="$2"
    if [ ! -f "$modelfile" ]; then
        warn "Modelfile not found: $modelfile — skipping $name"
        return
    fi
    log "Creating model: $name from $modelfile ..."
    ollama create "$name" -f "$modelfile" || warn "Failed to create $name (non-fatal)"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

create_model "her-coder"    "$SCRIPT_DIR/Modelfile.HER"
create_model "hal-reviewer" "$SCRIPT_DIR/Modelfile.HAL"
create_model "claw-agent"   "$SCRIPT_DIR/Modelfile.CLAW"

# ── 4. Verify ─────────────────────────────────────────────────────────
log "Installed models:"
ollama list

log ""
log "HELEN OS sub-agents ready."
log "  HER  → her-coder     (C-layer coding proposals)"
log "  HAL  → hal-reviewer  (G-layer code review)"
log "  CLAW → claw-agent    (Skills: Telegram, web, notify)"
log ""
log "Run tests: python -m pytest tests/test_agents.py -v"
