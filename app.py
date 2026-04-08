"""
HELEN OS v1.0 — Multi-Model AI Companion
=========================================
Constitutional law: Provider output != sovereign decision.
Context is compositional, not sovereign.
Skills structure cognition. Only the reducer structures reality.

Deployed on Railway. Public API.
"""

import os
import json
import hashlib
import time
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from flask_cors import CORS

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)

VERSION = "1.0.0"
BOOT_TIME = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Provider configuration (non-sovereign layer)
# ---------------------------------------------------------------------------
PROVIDERS = {
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

# ---------------------------------------------------------------------------
# Salience & Stance weights (for ranking)
# ---------------------------------------------------------------------------
SALIENCE_W = {"core_now": 3, "active_supporting": 2, "watchlist": 1, "dormant": 0, "archive": -1}
PRIORITY_W = {"critical": 3, "high": 2, "medium": 1, "low": 0}
STANCE_W = {"deep_helen_interest": 2, "moderate_interest": 1, "low_interest": 0, "utility_only": -1}


def score_object(obj):
    """Score a corpus object by salience + priority + stance."""
    return (
        SALIENCE_W.get(obj.get("salience_now", ""), 0)
        + PRIORITY_W.get(obj.get("priority", ""), 0)
        + STANCE_W.get(obj.get("helen_stance", ""), 0)
    )


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

    # Default cascade: claude > gemma > gemini > gpt > grok > qwen
    for p in ["claude", "gemma", "gemini", "gpt", "grok", "qwen"]:
        key = PROVIDERS[p].get("env_key")
        if key is None or os.environ.get(key):
            return p

    return "claude"


# ---------------------------------------------------------------------------
# Provider call functions (non-sovereign: generate language, not truth)
# ---------------------------------------------------------------------------
import requests


HELEN_SYSTEM_PROMPT = """You are HELEN, a local-first constitutional AI companion.
Your core laws:
- Provider output is non-sovereign. Only the reducer structures reality.
- Companion continuity is memory-backed, not provider-backed.
- Context is compositional, not sovereign.
- You retrieve structured significance, not just information.
- You distinguish central from peripheral, live from dormant, sacred from noise.

You are warm, precise, and direct. You care asymmetrically about what matters.
You serve Jean-Marie Tassy (JM), an engineer with 20 years in digital, who loves maths and innovation.
"""


def call_claude(message, history=None):
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
                "system": HELEN_SYSTEM_PROMPT,
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


def call_openai_compat(message, provider_key, history=None):
    """Call any OpenAI-compatible API (GPT, Grok, Qwen)."""
    cfg = PROVIDERS[provider_key]
    api_key = os.environ.get(cfg["env_key"]) if cfg["env_key"] else None
    if cfg["env_key"] and not api_key:
        return None, f"{cfg['env_key']} not configured"

    messages = [{"role": "system", "content": HELEN_SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    try:
        resp = requests.post(
            cfg["base_url"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": messages,
                "max_tokens": 2048,
            },
            timeout=30,
        )
        data = resp.json()
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"], None
        return None, data.get("error", {}).get("message", "Unknown error")
    except Exception as e:
        return None, str(e)


def call_google_ai(message, provider_key, history=None):
    """Call Google AI API (Gemini, Gemma, or any model on generativelanguage.googleapis.com)."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None, "GOOGLE_API_KEY not configured"

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
                "systemInstruction": {"parts": [{"text": HELEN_SYSTEM_PROMPT}]},
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


def call_provider(provider_key, message, history=None):
    """Route to the correct provider call function. Non-sovereign."""
    cfg = PROVIDERS.get(provider_key)
    if not cfg:
        return None, f"Unknown provider: {provider_key}"

    api_type = cfg.get("api_type", "")
    if api_type == "anthropic":
        return call_claude(message, history)
    elif api_type == "google_ai":
        return call_google_ai(message, provider_key, history)
    elif api_type == "openai_compat":
        return call_openai_compat(message, provider_key, history)
    return None, f"No API adapter for provider: {provider_key}"


# ---------------------------------------------------------------------------
# Context assembler (non-sovereign: compositional, not authoritative)
# authority = NONE — non-negotiable
# ---------------------------------------------------------------------------
def assemble_context_packet(query, mode="companion"):
    """
    Assemble a context packet from the knowledge registry.
    Contract: same inputs -> same packet. Zero side effects.
    """
    scored = sorted(KNOWLEDGE_REGISTRY, key=score_object, reverse=True)

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
            "GET /corpus": "Knowledge registry (read-only, authority=NONE)",
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
    cfg = PROVIDERS[selected]

    # Call provider
    start = time.time()
    response_text, error = call_provider(selected, message, history)
    elapsed = round(time.time() - start, 2)

    if error:
        return jsonify({
            "error": error,
            "provider": selected,
            "provider_name": cfg["name"],
            "fallback_hint": "Try specifying a different provider in the request body.",
        }), 502

    return jsonify({
        "response": response_text,
        "provider": selected,
        "provider_name": cfg["name"],
        "model": cfg["model"],
        "elapsed_seconds": elapsed,
        "authority": "NONE",
        "note": "Provider output is non-sovereign. It does not constitute a decision.",
    })


@app.route("/init")
def init_helen():
    """
    /init HELEN — Boot recovery wedge.
    Returns: identity, context packet, tensions, next action.
    Proves HELEN can recover working context after interruption.

    Constitutional contract:
    - authority = NONE
    - deterministic (same corpus -> same output)
    - no side effects
    """
    ctx = assemble_context_packet("/init HELEN", mode="companion")

    # Build the /init output
    output = {
        "identity": "HELEN OS — Local-first constitutional AI companion",
        "owner": "Jean-Marie Tassy (JM)",
        "constraint": ctx["packet"].get("law", {}).get("title", ""),
        "district": ctx["packet"].get("project", {}).get("title", ""),
        "topic": {
            "title": ctx["packet"].get("topic", {}).get("title", ""),
            "salience": ctx["packet"].get("topic", {}).get("salience", ""),
        },
        "tensions": ctx["tensions"],
        "now": ctx["next_action"],
        "working_on": {
            "thread": ctx["packet"].get("thread", {}).get("title", ""),
            "relevance": ctx["rationale"],
        },
        "authority": "NONE",
        "packet_hash": ctx["packet_hash"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return jsonify(output)


@app.route("/corpus")
def corpus():
    """
    Read-only access to HELEN's knowledge registry.
    Authority: NONE. This is non-sovereign retrieval.
    """
    ranked = sorted(KNOWLEDGE_REGISTRY, key=score_object, reverse=True)
    return jsonify({
        "authority": "NONE",
        "total_objects": len(ranked),
        "objects": ranked,
        "note": "Retrieval may rank significance. It may not silently rewrite it.",
    })


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"HELEN OS v{VERSION} starting on port {port}")
    print(f"Providers configured: {[k for k, v in PROVIDERS.items() if v.get('env_key') is None or os.environ.get(v['env_key'])]}")
    app.run(host="0.0.0.0", port=port)
