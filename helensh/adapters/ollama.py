"""HELEN OS — Ollama HTTP adapter.

Thin urllib-only client — no third-party HTTP libs.
All network I/O is isolated here so agents can mock it cleanly.

Design constraints:
  - urllib only (no requests, httpx, etc.)
  - raises OllamaError on any network/decode failure
  - is_available() / has_model() never raise — return bool
  - authority: False enforced at adapter boundary (model cannot claim authority)
"""
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# ── Exception ─────────────────────────────────────────────────────────


class OllamaError(Exception):
    """Raised on any Ollama communication failure."""


# ── Client ────────────────────────────────────────────────────────────

_DEFAULT_BASE = "http://localhost:11434"
_DEFAULT_TIMEOUT = 120  # seconds


class OllamaClient:
    """Minimal synchronous Ollama client.

    All methods that communicate with Ollama may raise OllamaError.
    is_available() and has_model() return bool and never raise.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── Low-level ─────────────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        """POST JSON body to path, return parsed response dict."""
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise OllamaError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(f"Cannot reach Ollama at {url}: {exc.reason}") from exc
        except Exception as exc:
            raise OllamaError(f"Unexpected error calling {url}: {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Invalid JSON from {url}: {exc}") from exc

    def _get(self, path: str) -> dict:
        """GET path, return parsed response dict."""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise OllamaError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(f"Cannot reach Ollama at {url}: {exc.reason}") from exc
        except Exception as exc:
            raise OllamaError(f"Unexpected error calling {url}: {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Invalid JSON from {url}: {exc}") from exc

    # ── Public API ────────────────────────────────────────────────────

    def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> str:
        """Generate a completion. Returns the response string.

        Uses /api/generate (single-turn, raw text).
        """
        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if system:
            body["system"] = system
        result = self._post("/api/generate", body)
        return result.get("response", "")

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> str:
        """Chat completion. Returns assistant message string.

        Uses /api/chat (multi-turn message list).
        messages: list of {"role": "user"|"assistant"|"system", "content": "..."}
        """
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        body: Dict[str, Any] = {
            "model": model,
            "messages": all_messages,
            "stream": stream,
            "options": {"temperature": temperature},
        }
        result = self._post("/api/chat", body)
        msg = result.get("message", {})
        return msg.get("content", "")

    def list_models(self) -> List[str]:
        """Return list of locally available model names."""
        result = self._get("/api/tags")
        models = result.get("models", [])
        return [m.get("name", "") for m in models if m.get("name")]

    def is_available(self) -> bool:
        """Ping Ollama. Returns True if reachable, False otherwise. Never raises."""
        try:
            self._get("/api/tags")
            return True
        except OllamaError:
            return False

    def has_model(self, model: str) -> bool:
        """Check if a specific model is locally available. Never raises."""
        try:
            return model in self.list_models()
        except OllamaError:
            return False

    def pull(self, model: str) -> None:
        """Pull (download) a model. Blocks until complete. May raise OllamaError."""
        self._post("/api/pull", {"name": model, "stream": False})


# ── Module exports ────────────────────────────────────────────────────

__all__ = ["OllamaClient", "OllamaError"]
