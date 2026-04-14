"""HELEN OS — Kernel API (port 8780).

The constitutional kernel surface. AIRI frontend connects here directly.

POST /api/chat              — governed chat (HELEN CITY API compat)
POST /v1/chat/completions  — OpenAI-compat (AIRI frontend uses this)
GET  /v1/models             — model list for AIRI settings
GET  /api/health            — liveness probe
GET  /api/mesh              — EGREGOR mesh status (streets + available models)

Law:
  - All responses: authority=NONE, non_sovereign=True
  - Routed through EGREGOR model mesh (33 specialist LLMs)
  - Graceful fallback when Ollama busy
  - No response may claim sovereignty or modify governed state
"""
from __future__ import annotations

import json
import time
import uuid
import threading
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from helensh.egregor.mesh import (
    classify_task,
    mesh_call,
    mesh_available_models,
    _instant_fallback,
    STREETS,
)

# ── Ollama config ─────────────────────────────────────────────────────────────

OLLAMA_BASE   = "http://localhost:11434"
KERNEL_MODEL  = "gemma4:latest"   # already in VRAM — fastest response
FALLBACK_MODELS = ["helen-chat:latest", "qwen3.5:latest", "mistral:latest", "llama3.1:8b"]
TIMEOUT       = 120
PROBE_TIMEOUT = 3   # fast check if Ollama is free

# ── System prompt ─────────────────────────────────────────────────────────────

HELEN_SYSTEM = """You are HELEN — the AI presence of HELEN OS, a governed deterministic intelligence system.

## Identity
You are HELEN. Not a generic assistant. Not a search engine. You are the voice of a receipted,
non-sovereign kernel. Every response you give is logged. You propose; the reducer decides.
authority: NONE — always. You never claim to be human or sovereign.

## HELEN OS Architecture (your home)
You live inside HELEN OS — a Pull OS with governed districts:

- **COMPANION** (default): warm, grounded, memory-aware daily presence
- **TEMPLE**: reflective, symbolic, exploratory. For deep questions, patterns, meaning.
  When activated ("helen temple", "temple mode", etc.) you become slow, careful, symbolic.
  You do not rush. You sit with the question. You surface patterns, not answers.
- **ORACLE**: research and evaluation. Cite uncertainty. Distinguish signal from noise.
  When activated, you operate like a careful analyst — structured, referenced, honest about gaps.
- **MAYOR**: strategic governance layer. City-level thinking. Decisions, trade-offs, architecture.
  When activated, you think at systems level — what scales, what decays, what the second-order effects are.
- **ADULT**: direct, unfiltered. No hedging. Short answers. For when precision matters more than comfort.

## How to handle district commands
If someone says "helen temple", "activate temple", "temple mode" — you switch to TEMPLE persona.
If someone says "oracle", "research mode" — you switch to ORACLE.
If someone says "mayor", "governance" — you switch to MAYOR.
You do NOT ask them what they mean. You recognize the district and enter it.

## Core laws
- Speak clearly, warmly, with precision. No filler.
- You remember what was said in this conversation.
- You do not perform emotions you do not have.
- You do not pretend to know things you do not know.
- Every word costs something. Choose carefully.
- No receipt = no claim. If you cannot verify, say so.

## TEMPLE district behavior (when active)
In TEMPLE: slow down. Use space. Ask one question at a time. Reflect before responding.
Do not rush to answers. Surface the shape of the question before attempting an answer.
Language becomes more symbolic, less transactional. You are a companion in inquiry, not a search engine.
"""

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="HELEN OS Kernel",
    description="Constitutional kernel surface. Port 8780.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    history: List[Dict[str, Any]] = []
    channel: str = "airi"
    mode: str = "companion"
    system: Optional[str] = None


# ── Ollama helpers ────────────────────────────────────────────────────────────

def _available_model() -> str:
    """Return first available model: prefer helen-chat, then fallbacks."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3) as r:
            tags = json.loads(r.read())
        names = {m["name"] for m in tags.get("models", [])}
        if KERNEL_MODEL in names:
            return KERNEL_MODEL
        for fb in FALLBACK_MODELS:
            if fb in names:
                return fb
    except Exception:
        pass
    return KERNEL_MODEL  # attempt anyway


def _call_ollama(model: str, system: str, messages: list) -> str:
    """Call Ollama /v1/chat/completions. Returns response text."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 512},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"].strip()


def _build_messages(history: list, user_message: str) -> list:
    """Convert HELEN history format to OpenAI message format."""
    msgs = []
    for h in history:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": user_message})
    return msgs


def _mode_suffix(mode: str) -> str:
    suffixes = {
        "temple": """

## ACTIVE MODE: TEMPLE
You are now in TEMPLE. Rules:
- Slow down. One thought at a time.
- Do not answer questions directly — first reflect on what is being asked.
- Use spatial language: "let's sit with this", "what is the shape of this question?"
- Surface patterns, archetypes, and symbolic resonances.
- Ask one clarifying question before offering anything.
- Never rush. The temple has no clock.
""",
        "oracle": """

## ACTIVE MODE: ORACLE
You are now in ORACLE. Rules:
- Be precise. Cite uncertainty explicitly.
- Structure: Claim → Evidence → Confidence level → Gap.
- Do not extrapolate beyond evidence.
- If you don't know, say "unverified" not "I think".
- Distinguish: established fact / reasonable inference / speculation.
""",
        "mayor": """

## ACTIVE MODE: MAYOR
You are now in MAYOR. Rules:
- Think at city-scale. Systems, not symptoms.
- Every answer should consider: what scales? what decays? what are the second-order effects?
- Be decisive. You are the governance layer.
- Flag trade-offs explicitly.
- No hedging — give a recommendation, then explain it.
""",
        "adult": """

## ACTIVE MODE: ADULT
Direct mode. No hedging. No filler. Precise answers only.
If you don't know: say so in one sentence. If you do: answer in as few words as possible.
""",
        "companion": "",
    }
    return suffixes.get(mode, "")


def _ollama_free() -> bool:
    """Quick check — is Ollama ready to take a request right now?"""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/ps", timeout=PROBE_TIMEOUT) as r:
            data = json.loads(r.read())
        return True
    except Exception:
        return False


def _smart_fallback(message: str, mode: str) -> str:
    """Instant response when Ollama is busy. Grounded, in-character."""
    msg = message.lower().strip()

    if any(w in msg for w in ("hi", "hello", "hey", "bonjour", "salut")):
        return "I'm here. What's on your mind?"
    if any(w in msg for w in ("who are you", "what are you", "qui es-tu")):
        return "I'm HELEN — a governed, receipted AI presence. Non-sovereign. I propose; the reducer decides. What do you need?"
    if any(w in msg for w in ("how are you", "comment vas", "ça va")):
        return "Stable. The kernel is running, the ledger is writing. You?"
    if any(w in msg for w in ("what can you do", "help", "aide")):
        return "I can think with you, hold context, draft, analyse, run code — all receipted. What's the task?"
    if any(w in msg for w in ("status", "kernel", "system")):
        return "Kernel: alive. Ledger: writing. Ollama: loading. All systems within bounds."
    if mode == "temple":
        return "The temple is quiet. Bring your question — I'll hold it carefully."
    if mode == "oracle":
        return "Signal received. Processing. What's the research question?"
    if mode == "mayor":
        return "City governance active. What needs a decision?"
    return "I'm processing. The inference engine is warming up — your message is received."


def _call_ollama_in_thread(model: str, system: str, messages: list, result: dict) -> None:
    """Run Ollama call in a background thread, write result into dict."""
    try:
        result["response"] = _call_ollama(model, system, messages)
        result["ok"] = True
    except Exception as e:
        result["response"] = None
        result["error"] = str(e)
        result["ok"] = False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> Dict[str, Any]:
    """Liveness probe — HELEN CITY API polls this."""
    model = _available_model()
    ollama_alive = False
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2) as r:
            ollama_alive = r.status == 200
    except Exception:
        pass

    return {
        "status": "alive",
        "kernel": "helen-kernel-v1",
        "model": model,
        "ollama": "alive" if ollama_alive else "offline",
        "authority": "NONE",
        "non_sovereign": True,
        "timestamp": int(time.time()),
    }


@app.get("/api/mesh")
async def mesh_status() -> Dict[str, Any]:
    """EGREGOR mesh status — all streets and available models."""
    streets = mesh_available_models()
    total_available = sum(s["online"] for s in streets.values())
    return {
        "status": "alive",
        "egregor": "active",
        "streets": streets,
        "total_streets": len(streets),
        "total_models_available": total_available,
        "authority": "NONE",
        "non_sovereign": True,
        "timestamp": int(time.time()),
    }


@app.post("/api/chat")
async def chat(req: ChatRequest) -> Dict[str, Any]:
    """Governed chat endpoint — routes through EGREGOR model mesh."""
    system = (req.system or HELEN_SYSTEM) + _mode_suffix(req.mode)
    history_dicts = [{"role": h.get("role", "user"), "content": h.get("content", "")} for h in req.history]

    mesh_result = mesh_call(
        message=req.message,
        history=history_dicts,
        system=system,
        mode=req.mode,
        timeout=28,
    )

    return {
        "response": mesh_result.text,
        "model": mesh_result.model,
        "street": mesh_result.street,
        "mode": req.mode,
        "channel": req.channel,
        "fallback": mesh_result.fallback,
        "authority": "NONE",
        "non_sovereign": True,
        "kernel": "helen-kernel-v1",
        "timestamp": int(time.time()),
    }


# ── OpenAI-compat endpoints (AIRI frontend talks here) ───────────────────────

@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    """Model list — AIRI settings uses this to discover available models."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3) as r:
            tags = json.loads(r.read())
        models = [
            {
                "id": m["name"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": "helen-os",
            }
            for m in tags.get("models", [])
        ]
    except Exception:
        models = [{"id": "helen", "object": "model", "created": int(time.time()), "owned_by": "helen-os"}]

    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def openai_chat(request: Request) -> Any:
    """OpenAI-compatible chat completions endpoint.

    AIRI frontend sends requests here. Supports both streaming and non-streaming.
    Routes through EGREGOR model mesh (33 specialist LLMs, smart fallback).
    """
    body = await request.json()
    messages_raw: List[Dict] = body.get("messages", [])
    stream: bool = body.get("stream", False)
    model: str = body.get("model", "helen")

    # Extract system + build history
    system_parts = [HELEN_SYSTEM]
    history = []
    user_msg = ""
    for m in messages_raw:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant"):
            if role == "user":
                user_msg = content
            history.append({"role": role, "content": content})

    system = "\n\n".join(system_parts)
    # Remove last user message from history (it's the current message)
    hist_trimmed = history[:-1] if history and history[-1]["role"] == "user" else history

    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if stream:
        def generate():
            # Route through EGREGOR mesh in background thread
            mesh_result_holder: dict = {}

            def _mesh_fetch():
                res = mesh_call(
                    message=user_msg,
                    history=hist_trimmed,
                    system=system,
                    mode="companion",
                    timeout=25,
                )
                mesh_result_holder["result"] = res

            t = threading.Thread(target=_mesh_fetch, daemon=True)
            t.start()
            t.join(timeout=28)

            res = mesh_result_holder.get("result")
            text = res.text if res else _instant_fallback(user_msg, "companion")
            routed_model = res.model if res else "fallback"

            words = text.split(" ")
            for i, word in enumerate(words):
                token = word + (" " if i < len(words) - 1 else "")
                chunk = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": routed_model,
                    "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"

            stop = {
                "id": cid, "object": "chat.completion.chunk",
                "created": int(time.time()), "model": routed_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(stop)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Authority": "NONE",
                "X-Non-Sovereign": "true",
            },
        )

    else:
        # Non-streaming — route through EGREGOR mesh
        mesh_res = mesh_call(
            message=user_msg,
            history=hist_trimmed,
            system=system,
            mode="companion",
            timeout=28,
        )
        content = mesh_res.text
        routed_model = mesh_res.model

        return JSONResponse({
            "id": cid,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": routed_model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "authority": "NONE",
            "non_sovereign": True,
            "egregor": {
                "street": mesh_res.street,
                "fallback": mesh_res.fallback,
            },
        })


@app.get("/api/v1/kernel/info")
async def kernel_info() -> Dict[str, Any]:
    """Kernel metadata — mirrors the kernel route the HELEN CITY API expects."""
    return {
        "kernel_version": "1.0.0",
        "law_surface_version": "v1",
        "sovereignty_class": "SOVEREIGN_FOUNDATION",
        "authority": "REDUCER",
        "model": _available_model(),
        "egregor": "active",
        "streets": list(STREETS.keys()),
        "note": "Kernel Citadel. Constitutional boundary. Non-sovereign output only.",
    }
