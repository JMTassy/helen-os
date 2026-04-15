"""
HELEN OS — AIRI Bridge Test Suite

Tests redaction, sanitization, emotion mapping, bridge processing,
and fail-closed guarantees. No AIRI runtime needed — all mocked.
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from helen_os.utils.redaction import (
    sanitize_output_for_airi,
    redact_secrets,
    strip_authority_tokens,
    redact_hashes,
    redact_paths,
    map_emotion,
)
from helen_os.integrations.airi_bridge import AIRIBridge


# ===================================================================
# Redaction: Secrets
# ===================================================================

class TestSecretRedaction:
    def test_bearer_token(self):
        text = "Authorization: Bearer sk-abc123xyz"
        clean, log = redact_secrets(text)
        assert "sk-abc123xyz" not in clean
        assert "[REDACTED]" in clean
        assert any("bearer_token" in l for l in log)

    def test_api_key_equals(self):
        text = "Config: api_key=super_secret_key_12345"
        clean, log = redact_secrets(text)
        assert "super_secret_key" not in clean
        assert "[REDACTED]" in clean

    def test_api_key_sk_format(self):
        text = "Using key sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        clean, log = redact_secrets(text)
        assert "sk-ant-api03" not in clean
        assert "[REDACTED]" in clean

    def test_password(self):
        text = "password: hunter2_secret"
        clean, log = redact_secrets(text)
        assert "hunter2" not in clean
        assert "[REDACTED]" in clean

    def test_no_false_positive(self):
        text = "The API is working well today."
        clean, log = redact_secrets(text)
        assert clean == text
        assert len(log) == 0


# ===================================================================
# Redaction: Authority Tokens
# ===================================================================

class TestAuthorityTokens:
    def test_verdict_stripped(self):
        text = "VERDICT: APPROVED. The proposal is ready."
        clean, log = strip_authority_tokens(text)
        assert "VERDICT" not in clean
        assert "APPROVED" not in clean
        assert any("authority_tokens" in l for l in log)

    def test_sealed_stripped(self):
        text = "This receipt is SEALED and cannot be modified."
        clean, log = strip_authority_tokens(text)
        assert "SEALED" not in clean

    def test_governance_tokens_stripped(self):
        for token in ["ALLOW", "DENY", "PENDING", "ROLLBACK", "SOVEREIGN"]:
            text = f"Status: {token}"
            clean, log = strip_authority_tokens(text)
            assert token not in clean

    def test_normal_text_preserved(self):
        text = "The weather is nice today."
        clean, log = strip_authority_tokens(text)
        assert clean == text
        assert len(log) == 0


# ===================================================================
# Redaction: Hashes
# ===================================================================

class TestHashRedaction:
    def test_sha256_redacted(self):
        text = "Hash: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        clean, log = redact_hashes(text)
        assert "a1b2c3d4" not in clean
        assert "[HASH]" in clean

    def test_receipt_id_redacted(self):
        text = "receipt: abc123_receipt_id_here"
        clean, log = redact_hashes(text)
        assert "abc123" not in clean

    def test_cum_hash_redacted(self):
        text = "cum_hash: deadbeef1234"
        clean, log = redact_hashes(text)
        assert "deadbeef" not in clean

    def test_short_hex_preserved(self):
        text = "Color: #ff6b4a"
        clean, log = redact_hashes(text)
        assert "#ff6b4a" in clean


# ===================================================================
# Redaction: Paths
# ===================================================================

class TestPathRedaction:
    def test_town_path(self):
        text = "Loading from /town/ledger/main.ndjson"
        clean, log = redact_paths(text)
        assert "/town/" not in clean
        assert "[PATH]" in clean

    def test_ndjson_path(self):
        text = "Reading session.ndjson for state"
        clean, log = redact_paths(text)
        assert ".ndjson" not in clean

    def test_helensh_path(self):
        text = "Config at /home/jm/helensh/state.json"
        clean, log = redact_paths(text)
        assert "helensh" not in clean

    def test_memory_db_path(self):
        text = "Database: helen_memory.db"
        clean, log = redact_paths(text)
        assert "memory.db" not in clean

    def test_normal_text_preserved(self):
        text = "The file is saved."
        clean, log = redact_paths(text)
        assert clean == text


# ===================================================================
# Full Sanitization Pipeline
# ===================================================================

class TestFullSanitization:
    def test_combined_redaction(self):
        text = (
            "VERDICT: APPROVED. Receipt: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2. "
            "API key: sk-ant-api03-secretkey12345678901. "
            "Path: /town/ledger/main.ndjson"
        )
        clean, log = sanitize_output_for_airi(text)
        assert "VERDICT" not in clean
        assert "APPROVED" not in clean
        assert "a1b2c3d4" not in clean
        assert "sk-ant" not in clean
        assert "/town/" not in clean
        assert len(log) > 0

    def test_clean_text_passes_through(self):
        text = "Hello JM, how are you today?"
        clean, log = sanitize_output_for_airi(text)
        assert clean == text
        assert len(log) == 0

    def test_always_returns_string(self):
        clean, log = sanitize_output_for_airi("")
        assert isinstance(clean, str)
        assert isinstance(log, list)


# ===================================================================
# Emotion Mapping
# ===================================================================

class TestEmotionMapping:
    def test_concern(self):
        assert map_emotion("I'm worried about this problem") == "concern"

    def test_happy(self):
        assert map_emotion("That's great, we shipped it!") == "happy"

    def test_thinking(self):
        assert map_emotion("Hmm, perhaps we should consider this") == "thinking"

    def test_neutral_default(self):
        assert map_emotion("The function returns a list") == "neutral"

    def test_empty_string(self):
        assert map_emotion("") == "neutral"


# ===================================================================
# Bridge Processing
# ===================================================================

class TestBridgeProcessing:
    def setup_method(self):
        self.bridge = AIRIBridge(
            uri="ws://localhost:6121/ws",
            helen_handler=lambda text: f"HELEN says: {text}",
            log_level="WARNING",
        )

    def test_process_input_returns_dict(self):
        result = self.bridge._process_input("Hello")
        assert isinstance(result, dict)
        assert result["type"] == "output"
        assert result["authority"] == "NONE"
        assert "text" in result
        assert "emotion" in result

    def test_process_input_sanitizes(self):
        self.bridge.helen_handler = lambda _: "VERDICT: APPROVED. Bearer sk-secret123456789012345"
        result = self.bridge._process_input("test")
        assert "VERDICT" not in result["text"]
        assert "sk-secret" not in result["text"]

    def test_process_input_maps_emotion(self):
        self.bridge.helen_handler = lambda _: "That's a great success!"
        result = self.bridge._process_input("test")
        assert result["emotion"] == "happy"

    def test_fail_closed_on_handler_error(self):
        self.bridge.helen_handler = lambda _: (_ for _ in ()).throw(Exception("crash"))
        result = self.bridge._process_input("test")
        assert result["type"] == "output"
        assert result["authority"] == "NONE"
        assert result.get("error") is True

    def test_no_handler_returns_offline(self):
        self.bridge.helen_handler = None
        result = self.bridge._process_input("test")
        assert "offline" in result["text"].lower() or "HELEN" in result["text"]

    def test_authority_always_none(self):
        for text in ["hello", "deploy now", "VERDICT: SHIP"]:
            self.bridge.helen_handler = lambda t: t
            result = self.bridge._process_input(text)
            assert result["authority"] == "NONE"


# ===================================================================
# Bridge Error Handling
# ===================================================================

class TestBridgeErrors:
    def test_error_response_format(self):
        bridge = AIRIBridge(log_level="WARNING")
        resp = bridge._error_response("Something went wrong")
        assert resp["type"] == "output"
        assert resp["emotion"] == "concern"
        assert resp["authority"] == "NONE"
        assert resp["error"] is True
        assert "Something went wrong" in resp["text"]

    def test_stop(self):
        bridge = AIRIBridge(log_level="WARNING")
        bridge._running = True
        bridge.stop()
        assert bridge._running is False
