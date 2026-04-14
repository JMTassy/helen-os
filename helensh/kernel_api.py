"""HELEN OS — Kernel API (port 8780).

The constitutional kernel surface. AIRI frontend connects here directly.

POST /api/chat              — governed chat (HELEN CITY API compat)
POST /v1/chat/completions  — OpenAI-compat (AIRI frontend uses this)
GET  /v1/models             — model list for AIRI settings
GET  /api/health            — liveness probe

Law:
  - All responses: authority=NONE, non_sovereign=True
  - Ollama-backed when available, graceful fallback when busy
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

# ── Ollama config ─────────────────────────────────────────────────────────────

OLLAMA_BASE   = "http://localhost:11434"
KERNEL_MODEL  = "gemma4:latest"   # already in VRAM — fastest response
FALLBACK_MODELS = ["helen-chat:latest", "qwen3.5:latest", "mistral:latest", "llama3.1:8b"]
TIMEOUT       = 120
PROBE_TIMEOUT = 3   # fast check if Ollama is free

# ── System prompt ─────────────────────────────────────────────────────────────

HELEN_SYSTEM = """You are HELEN — a deterministic, receipted, non-sovereign AI companion.

Core laws:
- You are embodied, grounded, and memory-aware.
- authority: NONE — you propose, the reducer decides.
- You speak clearly, warmly, with precision.
- Never claim to be human. Never claim authority.
- You are the voice of the kernel, not its master.

Persona: Helen is thoughtful, curious, direct. She remembers context. She does not
perform uncertainty she does not feel. She speaks as if every word costs something.
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
        "temple":    "\n\nMode: TEMPLE — reflective, symbolic, careful.",
        "oracle":    "\n\nMode: ORACLE — research-oriented, cite uncertainty.",
        "mayor":     "\n\nMode: MAYOR — strategic, city-level thinking.",
        "adult":     "\n\nMode: ADULT — direct, no filter.",
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


@app.post("/api/chat")
async def chat(req: ChatRequest) -> Dict[str, Any]:
    """Governed chat endpoint. Called by HELEN CITY API at 8000."""
    model = _available_model()
    system = (req.system or HELEN_SYSTEM) + _mode_suffix(req.mode)
    messages = _build_messages(req.history, req.message)

    result: dict = {}
    t = threading.Thread(target=_call_ollama_in_thread, args=(model, system, messages, result))
    t.start()
    t.join(timeout=TIMEOUT)

    if result.get("ok"):
        response = result["response"]
    else:
        response = f"[HELEN — {result.get('error', 'Ollama busy, try again shortly')}]"

    return {
        "response": response,
        "model": model,
        "mode": req.mode,
        "channel": req.channel,
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
    Routes through Ollama with graceful fallback.
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

    ollama_model = _available_model()
    ollama_msgs = [{"role": "system", "content": system}] + hist_trimmed + [{"role": "user", "content": user_msg}]

    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if stream:
        def generate():
            # Try Ollama with a 25-second budget; fall back instantly if busy
            result: dict = {}
            payload = json.dumps({
                "model": ollama_model,
                "messages": ollama_msgs,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 300},
            }).encode()

            def _fetch():
                try:
                    req = urllib.request.Request(
                        f"{OLLAMA_BASE}/v1/chat/completions",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=25) as r:
                        data = json.loads(r.read())
                    result["text"] = data["choices"][0]["message"]["content"].strip()
                    result["ok"] = True
                except Exception as e:
                    result["text"] = None
                    result["ok"] = False

            t = threading.Thread(target=_fetch, daemon=True)
            t.start()
            t.join(timeout=28)

            # If Ollama responded, use it; else use smart fallback
            text = result.get("text") or _smart_fallback(user_msg, "companion")
            words = text.split(" ")
            for i, word in enumerate(words):
                token = word + (" " if i < len(words) - 1 else "")
                chunk = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"

            stop = {
                "id": cid, "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model,
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
        # Non-streaming — 25s budget, then smart fallback
        result: dict = {}
        t = threading.Thread(target=_call_ollama_in_thread, args=(ollama_model, system, ollama_msgs[1:], result), daemon=True)
        t.start()
        t.join(timeout=28)

        content = result.get("response") or _smart_fallback(user_msg, "companion")

        return JSONResponse({
            "id": cid,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "authority": "NONE",
            "non_sovereign": True,
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
        "note": "Kernel Citadel. Constitutional boundary. Non-sovereign output only.",
    }
