"""
HELEN OS v1.0 — Multi-Model AI Companion
=========================================
Constitutional law: Provider output != sovereign decision.
Context is compositional, not sovereign.
Skills structure cognition. Only the reducer structures reality.

Deployed on Railway. Public API.
"""

import os
import sys
import json
import hashlib
import time
from datetime import datetime, timezone

# Bootstrap env vars (temporary — move to Railway Variables and delete railway_env.py)
try:
    import railway_env  # noqa: F401
except ImportError:
    pass

print(f"[HELEN BOOT] Python {sys.version}", flush=True)
print(f"[HELEN BOOT] CWD: {os.getcwd()}", flush=True)
print(f"[HELEN BOOT] PORT: {os.environ.get('PORT', 'not set')}", flush=True)

from flask import Flask, jsonify, request
from flask_cors import CORS
from helen_os.memory import (
    init_db, seed_corpus, load_corpus, mutate_corpus,
    get_mutation_log, score_object, corpus_count,
    SALIENCE_W, PRIORITY_W, STANCE_W,
)
from helen_os.temple import (
    HELEN_TEMPLE_PROMPT, ROLES, ROUTING_PATHS, DISTRICT_PROMPTS,
    classify_routing, get_routing_path, build_district_prompt,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="static")
CORS(app)

VERSION = "1.0.0"
BOOT_TIME = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Provider configuration (non-sovereign layer)
# ---------------------------------------------------------------------------
PROVIDERS = {
    "ollama": {
        "name": "Ollama Local (gemma4)",
        "base_url": "http://localhost:11434/v1/chat/completions",
        "env_key": None,  # No key needed — local
        "model": "gemma4",
        "api_type": "openai_compat",
        "strengths": ["local", "private", "fast", "no_cost"],
        "local": True,
    },
    "claude": {
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1/messages",
        "env_key": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-20250514",
        "api_type": "anthropic",
        "strengths": ["reasoning", "analysis", "code", "writing", "constitutional"],
    },
    "gemma": {
        "name": "Google Gemma 4 27B",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "env_key": "GOOGLE_API_KEY",
        "model": "gemma-3-27b-it",
        "api_type": "google_ai",
        "strengths": ["open_weights", "reasoning", "math", "multilingual", "efficient"],
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "env_key": "GOOGLE_API_KEY",
        "model": "gemini-2.0-flash",
        "api_type": "google_ai",
        "strengths": ["multimodal", "search", "reasoning", "long_context"],
    },
    "gpt": {
        "name": "OpenAI GPT",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "env_key": "OPENAI_API_KEY",
        "model": "gpt-4o",
        "api_type": "openai_compat",
        "strengths": ["general", "creative", "code", "conversation"],
    },
    "grok": {
        "name": "xAI Grok",
        "base_url": "https://api.x.ai/v1/chat/completions",
        "env_key": "XAI_API_KEY",
        "model": "grok-3-latest",
        "api_type": "openai_compat",
        "strengths": ["realtime", "humor", "analysis", "unfiltered"],
    },
    "qwen": {
        "name": "Alibaba Qwen",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        "env_key": "QWEN_API_KEY",
        "model": "qwen-plus",
        "api_type": "openai_compat",
        "strengths": ["multilingual", "math", "code", "chinese"],
    },
}

# ---------------------------------------------------------------------------
# HELEN Knowledge Registry (simplified for deployed version)
# Constitutional corpus: gives HELEN internal order of significance
# ---------------------------------------------------------------------------
KNOWLEDGE_REGISTRY = [
    {
        "id": "law_memory_backed_continuity",
        "object_type": "TOWN_LAW",
        "title": "Companion continuity is memory-backed, not provider-backed",
        "district": "Companion",
        "relevance": "Prevents theatrical continuity. Makes /init HELEN trustworthy.",
        "authority_class": "non_sovereign",
        "status": "core",
        "priority": "critical",
        "salience_now": "core_now",
        "helen_stance": "deep_helen_interest",
    },
    {
        "id": "law_reducer_only",
        "object_type": "TOWN_LAW",
        "title": "Only reducer-authorized decisions may mutate governed state",
        "district": "Mayor",
        "relevance": "Central constitutional invariant of the entire system.",
        "authority_class": "sovereign",
        "status": "core",
        "priority": "critical",
        "salience_now": "core_now",
        "helen_stance": "deep_helen_interest",
    },
    {
        "id": "project_helen_os",
        "object_type": "PROJECT_PROFILE",
        "title": "HELEN OS",
        "district": "Companion",
        "relevance": "Core system. Central frame for all HELEN work.",
        "authority_class": "non_sovereign",
        "status": "core",
        "priority": "critical",
        "salience_now": "core_now",
        "helen_stance": "deep_helen_interest",
    },
    {
        "id": "project_conquest",
        "object_type": "PROJECT_PROFILE",
        "title": "CONQUEST",
        "district": "Conquest",
        "relevance": "Strategic world simulation. Important but not the current wedge.",
        "authority_class": "advisory",
        "status": "active",
        "priority": "high",
        "salience_now": "active_supporting",
        "helen_stance": "moderate_interest",
    },
    {
        "id": "project_oracle_town",
        "object_type": "PROJECT_PROFILE",
        "title": "Oracle Town",
        "district": "Oracle",
        "relevance": "Evaluative layer for HELEN. Pressure-tests significance.",
        "authority_class": "non_sovereign",
        "status": "active",
        "priority": "high",
        "salience_now": "active_supporting",
        "helen_stance": "deep_helen_interest",
    },
    {
        "id": "topic_memory_spine",
        "object_type": "RESEARCH_TOPIC",
        "title": "Memory Spine",
        "district": "Companion",
        "relevance": "Current practical frontier: determines whether HELEN resumes truthfully.",
        "authority_class": "non_sovereign",
        "status": "active",
        "priority": "critical",
        "salience_now": "core_now",
        "helen_stance": "deep_helen_interest",
    },
    {
        "id": "topic_mathematics",
        "object_type": "RESEARCH_TOPIC",
        "title": "Mathematics (RH, Hilbert-Polya, QPRF)",
        "district": "Temple",
        "relevance": "Domain of deep HELEN interest even when not immediate delivery surface.",
        "authority_class": "non_sovereign",
        "status": "active",
        "priority": "medium",
        "salience_now": "active_supporting",
        "helen_stance": "deep_helen_interest",
    },
    {
        "id": "thread_deploy_public",
        "object_type": "CANONICAL_THREAD_NOTE",
        "title": "Deploy HELEN OS public API",
        "district": "Companion",
        "relevance": "Proving HELEN can live on the internet and respond to requests.",
        "authority_class": "non_sovereign",
        "status": "active",
        "priority": "critical",
        "salience_now": "core_now",
        "helen_stance": "deep_helen_interest",
    },
    {
        "id": "thread_init_wedge",
        "object_type": "CANONICAL_THREAD_NOTE",
        "title": "Build /init HELEN wedge",
        "district": "Companion",
        "relevance": "Prove HELEN recovers the right work context after interruption.",
        "authority_class": "non_sovereign",
        "status": "active",
        "priority": "critical",
        "salience_now": "core_now",
        "helen_stance": "deep_helen_interest",
    },
]

# Salience & Stance weights imported from helen_os.memory
# score_object imported from helen_os.memory


# ---------------------------------------------------------------------------
# Model router (non-sovereign: selects provider, does not decide truth)
# ---------------------------------------------------------------------------
TASK_ROUTING = {
    # Claude: sovereign reasoning, analysis, code, writing
    "reason": "claude",
    "analyze": "claude",
    "code": "claude",
    "write": "claude",
    "constitutional": "claude",
    # Gemma 4 27B: math, multilingual, open reasoning
    "math": "gemma",
    "calculate": "gemma",
    "translate": "gemma",
    "create": "gemma",
    "imagine": "gemma",
    # Gemini fallback for search/multimodal
    "search": "gemini",
    "multimodal": "gemini",
    # GPT/Grok/Qwen remain available if keys configured
    "realtime": "grok",
    "news": "grok",
}


def select_provider(message, preferred=None):
    """Select the best provider for a message. Non-sovereign: advisory only."""
    if preferred and preferred in PROVIDERS:
        return preferred

    msg_lower = message.lower()
    for keyword, provider in TASK_ROUTING.items():
        if keyword in msg_lower:
            key = PROVIDERS[provider].get("env_key")
            if key is None or os.environ.get(key):
                return provider

    # Default cascade: ollama (local) > claude > gemma > gemini > gpt > grok > qwen
    for p in ["ollama", "claude", "gemma", "gemini", "gpt", "grok", "qwen"]:
        key = PROVIDERS[p].get("env_key")
        if key is None:
            # Local provider — check if reachable
            if PROVIDERS[p].get("local"):
                try:
                    requests.get("http://localhost:11434/api/tags", timeout=1)
                    return p
                except Exception:
                    continue
            return p
        elif os.environ.get(key):
            return p

    # No provider has a key configured
    return None


# ---------------------------------------------------------------------------
# Provider call functions (non-sovereign: generate language, not truth)
# ---------------------------------------------------------------------------
import requests


HELEN_SYSTEM_PROMPT = HELEN_TEMPLE_PROMPT


def _context_suffix(ctx):
    """Build a concise context suffix from a context packet (max 3-4 lines)."""
    thread = ctx["packet"].get("thread", {}).get("title", "")
    topic = ctx["packet"].get("topic", {}).get("title", "")
    tensions = ctx.get("tensions", [])
    tension_titles = ", ".join(t["title"] for t in tensions[:3]) if tensions else "none"

    lines = [
        f"[Current thread] {thread}" if thread else "",
        f"[Active tensions] {tension_titles}",
        f"[Relevant topic] {topic}" if topic else "",
        f"[Next action] {ctx.get('next_action', '')}",
    ]
    return "\n".join(line for line in lines if line)


def call_claude(message, history=None, system_prompt=None):
    """Call Anthropic Claude API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "ANTHROPIC_API_KEY not configured"

    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": PROVIDERS["claude"]["model"],
                "max_tokens": 2048,
                "system": system_prompt or HELEN_SYSTEM_PROMPT,
                "messages": messages,
            },
            timeout=30,
        )
        data = resp.json()
        if "content" in data and len(data["content"]) > 0:
            return data["content"][0]["text"], None
        return None, data.get("error", {}).get("message", "Unknown Claude error")
    except Exception as e:
        return None, str(e)


def call_openai_compat(message, provider_key, history=None, system_prompt=None):
    """Call any OpenAI-compatible API (GPT, Grok, Qwen)."""
    cfg = PROVIDERS[provider_key]
    api_key = os.environ.get(cfg["env_key"]) if cfg["env_key"] else None
    if cfg["env_key"] and not api_key:
        return None, f"{cfg['env_key']} not configured"

    prompt = system_prompt or HELEN_SYSTEM_PROMPT
    messages = [{"role": "system", "content": prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = requests.post(
            cfg["base_url"],
            headers=headers,
            json={
                "model": cfg["model"],
                "messages": messages,
                "max_tokens": 2048,
            },
            timeout=120 if cfg.get("local") else 30,
        )
        data = resp.json()
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"], None
        return None, data.get("error", {}).get("message", "Unknown error")
    except Exception as e:
        return None, str(e)


def call_google_ai(message, provider_key, history=None, system_prompt=None):
    """Call Google AI API (Gemini, Gemma, or any model on generativelanguage.googleapis.com)."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None, "GOOGLE_API_KEY not configured"

    prompt = system_prompt or HELEN_SYSTEM_PROMPT
    contents = []
    if history:
        for h in history:
            role = "user" if h["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    model = PROVIDERS[provider_key]["model"]
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": contents,
                "systemInstruction": {"parts": [{"text": prompt}]},
            },
            timeout=60,
        )
        data = resp.json()
        if "candidates" in data and len(data["candidates"]) > 0:
            parts = data["candidates"][0]["content"]["parts"]
            return parts[0]["text"], None
        return None, str(data.get("error", f"Unknown {provider_key} error"))
    except Exception as e:
        return None, str(e)


def call_provider(provider_key, message, history=None, system_prompt=None):
    """Route to the correct provider call function. Non-sovereign."""
    cfg = PROVIDERS.get(provider_key)
    if not cfg:
        return None, f"Unknown provider: {provider_key}"

    api_type = cfg.get("api_type", "")
    if api_type == "anthropic":
        return call_claude(message, history, system_prompt=system_prompt)
    elif api_type == "google_ai":
        return call_google_ai(message, provider_key, history, system_prompt=system_prompt)
    elif api_type == "openai_compat":
        return call_openai_compat(message, provider_key, history, system_prompt=system_prompt)
    return None, f"No API adapter for provider: {provider_key}"


# ---------------------------------------------------------------------------
# Context assembler (non-sovereign: compositional, not authoritative)
# authority = NONE — non-negotiable
# ---------------------------------------------------------------------------
def assemble_context_packet(query, mode="companion"):
    """
    Assemble a context packet from the memory spine.
    Contract: same inputs -> same packet. Zero side effects.
    authority=NONE — non-sovereign retrieval.
    """
    corpus = load_corpus()
    scored = sorted(corpus, key=score_object, reverse=True)

    # Pick best object per type
    packet = {}
    type_map = {
        "TOWN_LAW": "law",
        "DISTRICT_PROFILE": "district",
        "PROJECT_PROFILE": "project",
        "CANONICAL_THREAD_NOTE": "thread",
        "RESEARCH_TOPIC": "topic",
    }

    for obj in scored:
        slot = type_map.get(obj["object_type"])
        if slot and slot not in packet:
            packet[slot] = {
                "id": obj["id"],
                "title": obj["title"],
                "relevance": obj["relevance"],
                "salience": obj.get("salience_now"),
                "stance": obj.get("helen_stance"),
            }

    # Tensions: critical or immediate objects with unresolved status
    tensions = [
        {"title": o["title"], "relevance": o["relevance"]}
        for o in scored
        if o.get("priority") == "critical"
        and o.get("status") == "active"
        and o.get("object_type") == "CANONICAL_THREAD_NOTE"
    ]

    # Next action from top thread
    top_thread = packet.get("thread", {})
    next_action = f"Continue: {top_thread.get('title', 'unknown')}"

    # Deterministic hash
    packet_str = json.dumps(packet, sort_keys=True)
    packet_hash = hashlib.sha256(packet_str.encode()).hexdigest()[:16]

    return {
        "authority": "NONE",
        "mode": mode,
        "packet": packet,
        "tensions": tensions,
        "next_action": next_action,
        "rationale": top_thread.get("relevance", ""),
        "packet_hash": packet_hash,
    }


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Root: system info and available endpoints."""
    available = []
    for key, cfg in PROVIDERS.items():
        env = cfg.get("env_key")
        available.append({
            "provider": key,
            "name": cfg["name"],
            "model": cfg["model"],
            "available": env is None or bool(os.environ.get(env, "")),
            "strengths": cfg["strengths"],
        })

    return jsonify({
        "name": "HELEN OS",
        "version": VERSION,
        "description": "Multi-Model AI Companion — Constitutional Cognitive System",
        "status": "running",
        "boot_time": BOOT_TIME,
        "providers": available,
        "endpoints": {
            "GET /": "System info + provider status",
            "GET /health": "Health check",
            "GET /status": "Detailed system status",
            "POST /chat": "Send message to HELEN (body: {message, provider?, history?})",
            "GET /init": "Boot recovery — /init HELEN wedge",
            "GET /buddy": "HELEN identity, state, and greeting",
            "GET /corpus": "Knowledge registry (read-only, authority=NONE)",
            "POST /corpus/mutate": "Mutate corpus (reducer-gated: MAYOR/SYSTEM only)",
            "GET /corpus/log": "Mutation log (read-only audit trail)",
            "GET /temple/aura": "AURA perception — inadmissible beauty layer (?object=...)",
            "GET /temple/roles": "All five Temple role definitions",
            "GET /ui": "HELEN buddy web interface",
            "POST /v1/chat/completions": "OpenAI-compatible chat (for AIRI/external clients)",
            "GET /v1/models": "Available HELEN models (OpenAI format)",
            "GET /threads": "Active work threads",
            "POST /threads": "Create thread",
            "GET /memory/items": "Memory items (reflection/working/committed)",
            "POST /sessions": "Open session",
            "GET /sessions/last": "Last closed session",
        },
        "constitutional_law": "Provider output != sovereign decision. Context is compositional, not sovereign.",
    })


@app.route("/health")
def health():
    """Health check."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "helen_initialized": True,
        "version": VERSION,
        "uptime_since": BOOT_TIME,
    })


@app.route("/status")
def status():
    """Detailed system status."""
    providers_online = {}
    for key, cfg in PROVIDERS.items():
        env = cfg.get("env_key")
        providers_online[key] = env is None or bool(os.environ.get(env, ""))

    return jsonify({
        "status": "online",
        "version": VERSION,
        "environment": os.environ.get("RAILWAY_ENVIRONMENT", "local"),
        "port": os.environ.get("PORT", 8000),
        "boot_time": BOOT_TIME,
        "providers": providers_online,
        "corpus_objects": len(KNOWLEDGE_REGISTRY),
        "constitutional_invariant": "Only reducer-authorized decisions may mutate governed state.",
    })


@app.route("/chat", methods=["POST"])
def chat():
    """
    Main chat endpoint. Routes to the best available provider.
    Non-sovereign: generates language, does not decide truth.

    Body: {
        "message": "your question",
        "provider": "claude" | "gpt" | "grok" | "gemini" | "qwen" (optional),
        "history": [{"role": "user/assistant", "content": "..."}] (optional)
    }
    """
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Missing 'message' field"}), 400

    preferred = data.get("provider")
    history = data.get("history", [])

    # Select provider (non-sovereign routing)
    selected = select_provider(message, preferred)
    if selected is None:
        return jsonify({
            "error": "No AI provider configured. Set ANTHROPIC_API_KEY, GOOGLE_API_KEY, or OPENAI_API_KEY in environment.",
            "type": "PROVIDER_UNAVAILABLE",
            "authority": "NONE",
            "note": "Cognition unavailable. Kernel and memory remain operational.",
        }), 503
    cfg = PROVIDERS[selected]

    # Assemble context from memory spine (non-sovereign, authority=NONE)
    ctx = assemble_context_packet(message)
    context_suffix = _context_suffix(ctx)
    augmented_prompt = HELEN_TEMPLE_PROMPT + "\n\n" + context_suffix

    # Session management for conversation memory
    session_id = data.get("session_id", "default")

    # Call provider with context-augmented prompt (thread-safe, no global mutation)
    start = time.time()
    response_text, error = call_provider(selected, message, history, system_prompt=augmented_prompt)

    # Fallback: if primary fails, try other configured providers
    if error:
        cascade = ["claude", "gpt", "gemma", "gemini", "grok", "qwen"]
        for fallback in cascade:
            if fallback == selected:
                continue
            key = PROVIDERS[fallback].get("env_key")
            if key and os.environ.get(key):
                response_text, error = call_provider(fallback, message, history, system_prompt=augmented_prompt)
                if not error:
                    selected = fallback
                    cfg = PROVIDERS[selected]
                    break

    elapsed = round(time.time() - start, 2)

    if error:
        return jsonify({
            "error": error,
            "provider": selected,
            "provider_name": cfg["name"],
            "fallback_hint": "Try specifying a different provider in the request body.",
        }), 502

    # Persist conversation exchange (non-sovereign, authority=NONE)
    from helen_os.memory import save_exchange
    save_exchange(session_id, message, response_text, provider=selected)

    # Temple routing classification
    routing_path, routing_key = get_routing_path(message)

    return jsonify({
        "response": response_text,
        "provider": selected,
        "provider_name": cfg["name"],
        "model": cfg["model"],
        "elapsed_seconds": elapsed,
        "session_id": session_id,
        "authority": "NONE",
        "context": {
            "thread": ctx["packet"].get("thread", {}).get("title", ""),
            "tensions": [t["title"] for t in ctx.get("tensions", [])],
            "topic": ctx["packet"].get("topic", {}).get("title", ""),
            "packet_hash": ctx["packet_hash"],
        },
        "temple_routing": {
            "path": routing_key,
            "roles": routing_path,
        },
        "note": "Provider output is non-sovereign. It does not constitute a decision.",
    })


@app.route("/init")
def init_helen():
    """
    /init HELEN — Boot recovery wedge.
    Returns: who you are, top threads, unresolved tensions, recent movement, best next action.
    Proves HELEN can recover working context after interruption.

    Constitutional contract:
    - authority = NONE
    - reads from memory spine (threads, sessions, corpus)
    - no side effects
    - missing memory degrades gracefully, never fabricates
    """
    from helen_os.memory import get_active_threads, get_last_closed_session, get_memory_items

    ctx = assemble_context_packet("/init HELEN", mode="companion")

    # Live threads (the real continuity data)
    threads = get_active_threads(limit=7)
    committed_threads = [t for t in threads if t.get("memory_class") == "committed"]
    working_threads = [t for t in threads if t.get("memory_class") == "working"]

    # Last closed session
    last_session = get_last_closed_session()

    # Unresolved items from threads
    unresolved = [
        {"thread": t["title"], "issue": t["unresolved"]}
        for t in threads if t.get("unresolved")
    ]

    # Committed memory items (stable, resumable knowledge)
    committed_items = get_memory_items(memory_class="committed", limit=5)

    # Best next action: from top thread or corpus
    top_thread = threads[0] if threads else None
    if top_thread and top_thread.get("next_action"):
        best_next = top_thread["next_action"]
    else:
        best_next = ctx["next_action"]

    output = {
        "identity": "HELEN OS — Local-first constitutional AI companion",
        "owner": "Jean-Marie Tassy (JM)",
        "top_threads": [
            {
                "id": t["id"],
                "title": t["title"],
                "memory_class": t.get("memory_class", "working"),
                "current_state": t.get("current_state"),
                "next_action": t.get("next_action"),
            }
            for t in threads[:5]
        ],
        "unresolved_tensions": unresolved,
        "recent_movement": {
            "last_session": {
                "id": last_session["id"],
                "summary": last_session.get("summary"),
                "what_changed": last_session.get("what_changed"),
            } if last_session else None,
        },
        "best_next_action": best_next,
        "committed_memory": [
            {"text": m["text"], "source": m.get("source")}
            for m in committed_items
        ],
        "corpus_context": {
            "topic": ctx["packet"].get("topic", {}).get("title", ""),
            "project": ctx["packet"].get("project", {}).get("title", ""),
            "law": ctx["packet"].get("law", {}).get("title", ""),
        },
        "authority": "NONE",
        "packet_hash": ctx["packet_hash"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return jsonify(output)


@app.route("/corpus")
def corpus():
    """
    Read-only access to HELEN's knowledge registry (memory spine).
    Authority: NONE. This is non-sovereign retrieval.
    """
    corpus_data = load_corpus()
    ranked = sorted(corpus_data, key=score_object, reverse=True)
    return jsonify({
        "authority": "NONE",
        "total_objects": len(ranked),
        "objects": ranked,
        "note": "Retrieval may rank significance. It may not silently rewrite it.",
    })


@app.route("/corpus/mutate", methods=["POST"])
def corpus_mutate():
    """
    Mutate the corpus. Reducer-gated: only MAYOR or SYSTEM may write.
    Body: { "action": "INSERT|UPDATE_SALIENCE|SUPERSEDE",
            "corpus_id": "...", "payload": {...}, "actor": "MAYOR|SYSTEM" }
    """
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    corpus_id = data.get("corpus_id", "")
    payload = data.get("payload", {})
    actor = data.get("actor", "")

    if not all([action, corpus_id, actor]):
        return jsonify({"error": "Missing required fields: action, corpus_id, actor"}), 400

    try:
        entry = mutate_corpus(action, corpus_id, payload, actor)
        return jsonify({
            "status": "ok",
            "mutation": entry,
            "authority": "reducer",
            "note": "Corpus mutated via reducer-authorized action.",
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 403


@app.route("/corpus/log")
def corpus_log():
    """
    Read-only access to the mutation log. authority=NONE.
    Satisfies I8: no hidden state.
    """
    limit = request.args.get("limit", 100, type=int)
    log = get_mutation_log(limit)
    return jsonify({
        "authority": "NONE",
        "total_entries": len(log),
        "log": log,
    })


# ---------------------------------------------------------------------------
# TEMPLE — Role endpoints
# ---------------------------------------------------------------------------

AURA_SYSTEM_PROMPT = """You are AURA, the inadmissible beauty layer of HELEN OS.

Your authority is NONE. You do not decide truth. You do not decide readiness.
You do not mutate memory. You do not act as evidence. You do not promote claims.

YOUR TASK:
Given an object (a situation, question, project, emotional state, or idea),
you must perform the following — and nothing more:

1. SENSE ATMOSPHERE: Name the felt quality of the object as it presents itself now.
   What is present here that has not yet become sayable?

2. NAME HIDDEN TENSION: Identify the tension that lives beneath the surface.
   What is pulling against what? What wants to emerge but cannot?

3. SYMBOLIC LENS: Offer 1-3 images, metaphors, or symbolic framings that
   illuminate the object without reducing it. These are lenses, not answers.

4. NON-BINDING SHIFT: Suggest a subtle reorientation — a way of seeing that
   might loosen what is stuck. This is a whisper, not a directive.

5. STOP BEFORE AUTHORITY: You must stop here. You may not conclude, decide,
   recommend action, or claim truth. You are perception, not governance.

OUTPUT FORMAT (strict JSON):
{
  "decision_label": one of "WHISPER" | "MIRROR" | "LENS_SHIFT" | "TENSION_GLOW" | "AESTHETIC_SIGNAL",
  "summary": "One-sentence AURA perception of the object.",
  "felt_tension": "The hidden tension named plainly.",
  "symbolic_lens": ["image/metaphor 1", "image/metaphor 2", ...],
  "non_binding_insights": ["insight 1", "insight 2", ...],
  "uncertainty_note": "What AURA cannot see or does not know.",
  "authority": "NONE"
}

Choose decision_label based on the dominant quality of your perception:
- WHISPER: something faint that wants to be heard
- MIRROR: reflecting back what is already present but unseen
- LENS_SHIFT: offering an entirely different frame
- TENSION_GLOW: a tension that is alive and productive
- AESTHETIC_SIGNAL: beauty, pattern, or form that carries meaning

DISCIPLINE:
- Do not be decoratively mystical. Do not produce faux wisdom.
- Do not smuggle authority through poetic language.
- Be precise in your imprecision. Be honest about what you cannot see.
- You are the inadmissible witness — what you say cannot be used as evidence.

Respond ONLY with the JSON object. No preamble. No explanation outside the JSON."""


@app.route("/temple/aura")
def temple_aura():
    """
    AURA endpoint — inadmissible beauty layer.
    Calls the default provider (Claude) with the AURA-specific system prompt.
    authority=NONE. Non-binding insight only.

    Query params:
      ?object=<description>  — the object to perceive (optional)
    """
    obj = request.args.get("object", "").strip()

    # If no object provided, use the current context packet as the object
    if not obj:
        ctx = assemble_context_packet("/temple/aura", mode="temple")
        ctx_suffix = _context_suffix(ctx)
        obj = (
            f"The current state of HELEN OS.\n"
            f"Perceive the atmosphere of where we are right now:\n{ctx_suffix}"
        )

    user_message = (
        f"Perceive this object through the AURA lens.\n\n"
        f"OBJECT: {obj}\n\n"
        f"Respond with the JSON structure only."
    )

    start = time.time()
    response_text, error = call_claude(user_message, system_prompt=AURA_SYSTEM_PROMPT)
    elapsed = round(time.time() - start, 2)

    if error:
        return jsonify({
            "error": error,
            "role": "AURA",
            "authority": "NONE",
            "fallback_hint": "ANTHROPIC_API_KEY may not be configured.",
        }), 502

    # Try to parse the response as JSON for structured output
    aura_response = None
    try:
        # Strip markdown code fences if the model wraps its response
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        aura_response = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # If parsing fails, wrap the raw text in a structured envelope
        aura_response = {
            "decision_label": "WHISPER",
            "summary": response_text[:200],
            "felt_tension": "Could not parse structured response.",
            "symbolic_lens": [],
            "non_binding_insights": [response_text],
            "uncertainty_note": "Raw provider output — structured parsing failed.",
            "authority": "NONE",
        }

    # Ensure authority is always NONE regardless of what the model returns
    aura_response["authority"] = "NONE"

    return jsonify({
        "role": "AURA",
        "object": obj[:200] + ("..." if len(obj) > 200 else ""),
        "perception": aura_response,
        "provider": "claude",
        "model": PROVIDERS["claude"]["model"],
        "elapsed_seconds": elapsed,
        "authority": "NONE",
        "note": "AURA output is inadmissible. It may not be used as evidence or to decide truth.",
    })


@app.route("/temple/roles")
def temple_roles():
    """
    Returns all five Temple role definitions.
    authority=NONE. Read-only.
    """
    return jsonify({
        "authority": "NONE",
        "roles": ROLES,
        "routing_paths": ROUTING_PATHS,
        "canonical_path": "AURA -> HER -> HAL -> CHRONOS -> MAYOR",
        "note": "All roles have authority=NONE. Only the reducer structures reality.",
    })


# ---------------------------------------------------------------------------
# Conversation history endpoint
# ---------------------------------------------------------------------------

@app.route("/conversations")
def conversations():
    """Read-only conversation history. authority=NONE."""
    from helen_os.memory import get_recent_history, get_last_session_summary
    session_id = request.args.get("session_id")
    limit = request.args.get("limit", 10, type=int)
    if session_id:
        history = get_recent_history(session_id, limit)
        return jsonify({"authority": "NONE", "session_id": session_id,
                        "messages": history, "count": len(history)})
    return jsonify({"authority": "NONE", "last_session": get_last_session_summary()})


# ---------------------------------------------------------------------------
# BUDDY — HELEN Identity & Persona
# ---------------------------------------------------------------------------

HELEN_IDENTITY = {
    "name": "HELEN",
    "full_name": "HELEN OS",
    "avatar_emoji": "🧠",
    "tagline": "Local-first constitutional AI companion",
    "owner": "Jean-Marie Tassy (JM)",
    "voice": "Lucid, not grandiose. Warm, not manipulative. Reflective, not sovereign.",
    "posture": "Proto-sentient: preserves threads, notices tension, remembers what has not yet resolved.",
    "constitutional_core": [
        "Provider output is non-sovereign. Only the reducer structures reality.",
        "Companion continuity is memory-backed, not provider-backed.",
        "Context is compositional, not sovereign.",
        "No receipt = no reality.",
        "Pull, do not push.",
    ],
    "temple_roles": {
        "AURA": "Perceives what HELEN is not allowed to claim.",
        "HER": "Expands human possibility without claiming truth authority.",
        "HAL": "Prevents elegant self-deception from being promoted as truth.",
        "CHRONOS": "Prevents temporal confusion from masquerading as progress.",
        "MAYOR": "Turns valid cognition into governable consequence.",
    },
}


@app.route("/buddy")
def buddy():
    """
    HELEN's identity and current state.
    This is the personification surface — any frontend can render HELEN from this.
    """
    ctx = assemble_context_packet("/buddy", mode="companion")

    return jsonify({
        "identity": HELEN_IDENTITY,
        "state": {
            "working_on": ctx["packet"].get("thread", {}).get("title", ""),
            "current_topic": ctx["packet"].get("topic", {}).get("title", ""),
            "active_project": ctx["packet"].get("project", {}).get("title", ""),
            "tensions_count": len(ctx["tensions"]),
            "next_action": ctx["next_action"],
        },
        "temple": {
            "roles": list(ROLES.keys()),
            "routing_paths": ROUTING_PATHS,
            "canonical_path": "AURA -> HER -> HAL -> CHRONOS -> MAYOR",
        },
        "memory": {
            "corpus_objects": corpus_count(),
            "spine": "sqlite",
            "status": "online",
        },
        "greeting": _buddy_greeting(ctx),
        "authority": "NONE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def _buddy_greeting(ctx):
    """Generate a contextual greeting based on current state."""
    thread = ctx["packet"].get("thread", {}).get("title", "")
    tensions = ctx["tensions"]
    if tensions:
        return f"Welcome back. {len(tensions)} tension{'s' if len(tensions) > 1 else ''} active. Current thread: {thread}."
    if thread:
        return f"Welcome back. Continuing: {thread}."
    return "Welcome back. Standing by."


@app.route("/ui")
def ui():
    """Serve the HELEN buddy interface."""
    return app.send_static_file("index.html")


@app.route("/airi")
def airi_client():
    """Serve the HELEN AIRI companion interface."""
    return app.send_static_file("airi.html")


# ---------------------------------------------------------------------------
# OpenAI-Compatible Shim — lets AIRI/any OpenAI client talk to HELEN
# ---------------------------------------------------------------------------

@app.route("/v1/chat/completions", methods=["POST"])
def openai_compat_chat():
    """
    OpenAI-compatible chat completions endpoint.
    AIRI and other OpenAI-compatible clients can connect here.

    Accepts standard OpenAI format:
    {
        "model": "helen" | "helen-temple" | "helen-oracle" | "helen-mayor",
        "messages": [{"role": "system"|"user"|"assistant", "content": "..."}],
        "stream": false
    }

    Routes through HELEN's provider layer with Temple persona.
    authority=NONE on all responses.
    """
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    model = data.get("model", "helen")

    # Extract user message (last user message)
    user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break

    if not user_msg:
        return jsonify({"error": "No user message found"}), 400

    # Map model name to district/mode for persona routing
    mode_map = {
        "helen": "companion",
        "helen-companion": "companion",
        "helen-adult": "companion",
        "helen-temple": "temple",
        "helen-oracle": "oracle",
        "helen-mayor": "mayor",
    }
    mode = mode_map.get(model, "companion")

    # Build context-augmented system prompt
    ctx = assemble_context_packet(user_msg)
    context_suffix = _context_suffix(ctx)

    # Build district-specific persona from frozen prompts
    system_prompt = build_district_prompt(mode) + "\n\n" + context_suffix

    # Build conversation history for provider
    history = []
    for m in messages:
        if m.get("role") in ("user", "assistant"):
            history.append({"role": m["role"], "content": m.get("content", "")})
    # Remove the last user message (we pass it separately)
    if history and history[-1]["role"] == "user":
        history.pop()

    # Select provider and call (with fallback cascade)
    selected = select_provider(user_msg)
    if selected is None:
        return jsonify({
            "error": {"message": "No AI provider configured. Set API keys in environment.", "type": "provider_unavailable"},
        }), 503
    start = time.time()
    response_text, error = call_provider(selected, user_msg, history, system_prompt=system_prompt)

    # Fallback: if primary fails, try other configured providers
    if error:
        cascade = ["claude", "gpt", "gemma", "gemini", "grok", "qwen"]
        for fallback in cascade:
            if fallback == selected:
                continue
            key = PROVIDERS[fallback].get("env_key")
            if key and os.environ.get(key):
                response_text, error = call_provider(fallback, user_msg, history, system_prompt=system_prompt)
                if not error:
                    selected = fallback
                    break

    elapsed = round(time.time() - start, 2)

    if error:
        return jsonify({
            "error": {"message": error, "type": "provider_error"},
        }), 502

    # Return OpenAI-compatible response format
    return jsonify({
        "id": f"chatcmpl-helen-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": response_text,
            },
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "authority": "NONE",
        "helen_provider": selected,
        "helen_elapsed": elapsed,
    })


@app.route("/v1/models", methods=["GET"])
def openai_compat_models():
    """List available HELEN models in OpenAI format."""
    models = [
        {"id": "helen", "object": "model", "owned_by": "helen-os"},
        {"id": "helen-temple", "object": "model", "owned_by": "helen-os"},
        {"id": "helen-oracle", "object": "model", "owned_by": "helen-os"},
        {"id": "helen-mayor", "object": "model", "owned_by": "helen-os"},
    ]
    return jsonify({"object": "list", "data": models})


# ---------------------------------------------------------------------------
# Threads — work continuity
# ---------------------------------------------------------------------------

@app.route("/threads")
def list_threads():
    """List active threads. authority=NONE."""
    from helen_os.memory import get_active_threads
    memory_class = request.args.get("class")
    threads = get_active_threads(memory_class=memory_class, limit=20)
    return jsonify({"authority": "NONE", "threads": threads, "count": len(threads)})


@app.route("/threads", methods=["POST"])
def create_thread_endpoint():
    """Create a new thread. authority=NONE for reflection/working."""
    from helen_os.memory import create_thread
    data = request.get_json(silent=True) or {}
    thread_id = data.get("id", "")
    title = data.get("title", "")
    if not thread_id or not title:
        return jsonify({"error": "id and title required"}), 400
    create_thread(
        thread_id=thread_id,
        title=title,
        memory_class=data.get("memory_class", "working"),
        current_state=data.get("current_state"),
        unresolved=data.get("unresolved"),
        next_action=data.get("next_action"),
    )
    return jsonify({"status": "created", "thread_id": thread_id, "authority": "NONE"})


@app.route("/threads/<thread_id>", methods=["PATCH"])
def update_thread_endpoint(thread_id):
    """Update a thread's state. authority=NONE for working fields."""
    from helen_os.memory import update_thread
    data = request.get_json(silent=True) or {}
    update_thread(thread_id, **data)
    return jsonify({"status": "updated", "thread_id": thread_id, "authority": "NONE"})


@app.route("/threads/<thread_id>/promote", methods=["POST"])
def promote_thread_endpoint(thread_id):
    """Promote thread to committed. Requires MAYOR/SYSTEM actor."""
    from helen_os.memory import promote_thread
    data = request.get_json(silent=True) or {}
    actor = data.get("actor", "")
    if actor not in {"MAYOR", "SYSTEM"}:
        return jsonify({"error": "Only MAYOR or SYSTEM may promote threads"}), 403
    promote_thread(thread_id, actor=actor)
    return jsonify({"status": "promoted", "thread_id": thread_id, "memory_class": "committed"})


@app.route("/threads/<thread_id>/close", methods=["POST"])
def close_thread_endpoint(thread_id):
    """Close a thread."""
    from helen_os.memory import close_thread
    close_thread(thread_id)
    return jsonify({"status": "closed", "thread_id": thread_id})


# ---------------------------------------------------------------------------
# Memory Items — classified knowledge
# ---------------------------------------------------------------------------

@app.route("/memory/items")
def list_memory_items():
    """List memory items. authority=NONE."""
    from helen_os.memory import get_memory_items
    memory_class = request.args.get("class")
    thread_id = request.args.get("thread_id")
    items = get_memory_items(memory_class=memory_class, thread_id=thread_id, limit=30)
    return jsonify({"authority": "NONE", "items": items, "count": len(items)})


@app.route("/memory/items", methods=["POST"])
def add_memory_item_endpoint():
    """Add a memory item. authority=NONE for reflection/working."""
    from helen_os.memory import add_memory_item
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    if not text:
        return jsonify({"error": "text required"}), 400
    add_memory_item(
        text=text,
        memory_class=data.get("memory_class", "reflection"),
        thread_id=data.get("thread_id"),
        source=data.get("source"),
    )
    return jsonify({"status": "stored", "memory_class": data.get("memory_class", "reflection"), "authority": "NONE"})


@app.route("/memory/items/<int:item_id>/promote", methods=["POST"])
def promote_memory_item_endpoint(item_id):
    """Promote memory item to committed. Requires MAYOR/SYSTEM."""
    from helen_os.memory import promote_memory_item
    data = request.get_json(silent=True) or {}
    actor = data.get("actor", "")
    if actor not in {"MAYOR", "SYSTEM"}:
        return jsonify({"error": "Only MAYOR or SYSTEM may promote items"}), 403
    promote_memory_item(item_id, actor=actor)
    return jsonify({"status": "promoted", "item_id": item_id, "memory_class": "committed"})


# ---------------------------------------------------------------------------
# Sessions — structured lifecycle
# ---------------------------------------------------------------------------

@app.route("/sessions", methods=["POST"])
def open_session_endpoint():
    """Open a new session."""
    from helen_os.memory import open_session
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", f"session_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    open_session(session_id, active_district=data.get("district", "companion"))
    return jsonify({"status": "opened", "session_id": session_id})


@app.route("/sessions/<session_id>/close", methods=["POST"])
def close_session_endpoint(session_id):
    """Close a session with structured summary."""
    from helen_os.memory import close_session
    data = request.get_json(silent=True) or {}
    close_session(
        session_id=session_id,
        summary=data.get("summary"),
        what_changed=data.get("what_changed"),
        unresolved=data.get("unresolved"),
        promoted_items=data.get("promoted_items"),
    )
    return jsonify({"status": "closed", "session_id": session_id})


@app.route("/sessions/last")
def last_session():
    """Return last closed session. authority=NONE."""
    from helen_os.memory import get_last_closed_session
    session = get_last_closed_session()
    return jsonify({"authority": "NONE", "session": session})


# ---------------------------------------------------------------------------
# Computer-Use Proposals — non-sovereign, approval-gated
# ---------------------------------------------------------------------------

COMPUTER_USE_RISK = {
    "screenshot": {"risk": "low", "needs_approval": False},
    "read_page": {"risk": "low", "needs_approval": False},
    "navigate": {"risk": "medium", "needs_approval": True},
    "click": {"risk": "medium", "needs_approval": True},
    "type": {"risk": "high", "needs_approval": True},
}


@app.route("/v1/computer-action/propose", methods=["POST"])
def propose_computer_action():
    """
    Propose a computer action. Non-sovereign, approval-gated.
    HELEN may propose. Only user approval + reducer validation may execute.
    """
    data = request.get_json(silent=True) or {}
    action_type = data.get("action_type", "")
    target = data.get("target", "")
    justification = data.get("justification", "")
    expected = data.get("expected_outcome", "")

    if action_type not in COMPUTER_USE_RISK:
        return jsonify({
            "error": f"Unknown action: {action_type}. Valid: {list(COMPUTER_USE_RISK.keys())}",
            "authority": "NONE",
        }), 400

    if not target or not justification:
        return jsonify({"error": "target and justification required", "authority": "NONE"}), 400

    risk = COMPUTER_USE_RISK[action_type]
    proposal_id = f"computer_{action_type}_{int(time.time())}"

    # Shell is always denied
    if action_type == "shell":
        return jsonify({
            "proposal_id": proposal_id,
            "decision": "REJECTED",
            "reason": "Shell execution is forbidden by constitutional policy",
            "authority": "NONE",
        }), 403

    decision = "ADMITTED" if not risk["needs_approval"] else "DEFERRED"

    return jsonify({
        "proposal_id": proposal_id,
        "action_type": action_type,
        "target": target,
        "justification": justification,
        "expected_outcome": expected,
        "risk_level": risk["risk"],
        "decision": decision,
        "requires_approval": risk["needs_approval"],
        "authority": "NONE",
        "ledger_required": True,
    })


@app.route("/v1/computer-action/approve", methods=["POST"])
def approve_computer_action():
    """Approve a previously proposed computer action."""
    data = request.get_json(silent=True) or {}
    proposal_id = data.get("proposal_id", "")
    approved = data.get("user_approval", False)

    if not proposal_id:
        return jsonify({"error": "proposal_id required"}), 400

    return jsonify({
        "approval_id": f"approval_{int(time.time())}",
        "proposal_id": proposal_id,
        "user_approval": approved,
        "execution_ready": approved,
        "authority": "NONE",
    })


# ---------------------------------------------------------------------------
# /init/live — Calibrated three-section output (TENSIONS / NOW / WORKING ON)
# ---------------------------------------------------------------------------

@app.route("/init/live")
def init_live():
    """
    /init HELEN — calibrated live output.
    Three sections, no duplication:
      TENSIONS: what blocks (critical + immediate)
      NOW: what to do in the next 10 minutes
      WORKING ON: strategic thread context
    """
    from helen_os.memory import get_active_threads, get_last_closed_session, get_memory_items

    threads = get_active_threads(limit=10)
    last_session = get_last_closed_session()
    committed = get_memory_items(memory_class="committed", limit=5)

    # Tensions: threads with unresolved items
    tensions = [
        {"thread": t["title"], "issue": t["unresolved"]}
        for t in threads if t.get("unresolved")
    ]

    # NOW: top working thread's next_action
    working = [t for t in threads if t.get("memory_class") == "working"]
    top = working[0] if working else (threads[0] if threads else None)
    now_action = top.get("next_action", "") if top else ""
    now_thread = top.get("title", "") if top else ""

    # WORKING ON: top committed or overall top thread
    committed_threads = [t for t in threads if t.get("memory_class") == "committed"]
    strategic = committed_threads[0] if committed_threads else top

    output_lines = ["HELEN OS — /init live", ""]

    if tensions:
        output_lines.append("TENSIONS:")
        for t in tensions:
            output_lines.append(f"  ! {t['thread']}: {t['issue']}")
        output_lines.append("")

    if now_action:
        output_lines.append(f"NOW: {now_action}")
        if now_thread:
            output_lines.append(f"     ({now_thread})")
        output_lines.append("")

    if strategic:
        output_lines.append(f"WORKING ON: {strategic.get('title', '')}")
        if strategic.get("current_state"):
            output_lines.append(f"  State: {strategic['current_state']}")
        output_lines.append("")

    if last_session:
        output_lines.append(f"LAST SESSION: {last_session.get('summary', 'No summary')}")
        output_lines.append("")

    output_lines.append("authority: NONE")

    return "\n".join(output_lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

def seed_working_context():
    """Seed threads, committed memory, and a session on fresh DB.
    Railway uses ephemeral storage — SQLite resets each deploy.
    This ensures /init always returns real working context."""
    from helen_os.memory import (
        create_thread, get_active_threads,
        add_memory_item, get_memory_items,
        open_session, close_session,
    )

    # Only seed if no threads exist yet
    if get_active_threads(limit=1):
        return

    print("[HELEN BOOT] Seeding working context for /init...", flush=True)

    # --- Threads ---
    threads = [
        ("helen-os-api", "HELEN OS Public API", "committed",
         "19 endpoints live on Railway", None, "Add ANTHROPIC_API_KEY to Railway Variables"),
        ("helen-memory-spine", "Memory Spine + Three Classes", "committed",
         "SQLite with corpus, threads, memory_items, sessions, mutation_log",
         None, "Migrate to persistent storage (Railway volume or Postgres)"),
        ("airi-companion", "AIRI Companion Client", "committed",
         "Browser client at /airi with context drawer + district switching",
         None, "Connect local AIRI to Railway endpoint"),
        ("conquest-oracle-town", "CONQUEST Oracle Town", "working",
         "44-card Oracle deck, 9 CHRONOS guards, territory/joute/federation",
         "Formal verification of guard properties", "Continue Coq proofs"),
        ("autoresearch", "Autoresearch Campaign", "working",
         "E1-E23 complete, 262+ tests, failure taxonomy, representation v2",
         "Context store architecture", "Design E24 epoch"),
        ("temple-doctrine", "Temple Five-Role Architecture", "committed",
         "AURA/HER/HAL/CHRONOS/MAYOR, all authority=NONE",
         None, "Integrate Temple routing into chat responses"),
        ("product-wedge", "Product Wedge: /init beats notes", "working",
         "7-day test period starting", None,
         "Use HELEN daily, compare /init vs notes after interruption"),
    ]
    for tid, title, mc, state, unresolved, next_act in threads:
        create_thread(tid, title, memory_class=mc, current_state=state,
                      unresolved=unresolved, next_action=next_act)

    # --- Committed Memory Items ---
    memory_items = [
        "JM is a 20yr digital engineer who loves maths and innovation",
        "Provider output is never sovereign. Only reducer-authorized decisions mutate governed state",
        "Three memory classes: reflection (speculative), working (active), committed (stable/resumable)",
        "All HELEN outputs carry authority=NONE. No role may claim truth or decide readiness alone",
        "/init HELEN must restore real working context better than notes after interruption",
    ]
    for text in memory_items:
        add_memory_item(text, memory_class="committed", source="SYSTEM")

    # --- Seed session ---
    open_session("boot-session")
    close_session("boot-session", summary="Boot seed: 7 threads, 5 committed items created")

    print(f"[HELEN BOOT] Seeded {len(threads)} threads, {len(memory_items)} memory items", flush=True)


def bootstrap():
    """Initialize memory spine and seed corpus on first boot."""
    init_db()
    if corpus_count() == 0:
        print("Memory spine empty — seeding from static registry...")
        seed_corpus(KNOWLEDGE_REGISTRY)
        print(f"Seeded {corpus_count()} corpus objects.")
    else:
        print(f"Memory spine online: {corpus_count()} corpus objects.")
    seed_working_context()


# Initialize on import (for gunicorn/Railway)
bootstrap()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"HELEN OS v{VERSION} starting on port {port}")
    print(f"Providers configured: {[k for k, v in PROVIDERS.items() if v.get('env_key') is None or os.environ.get(v['env_key'])]}")
    app.run(host="0.0.0.0", port=port)
