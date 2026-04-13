"""Tests for URL/Fetch Gate — Lock 3.

Law:
  If input contains URL and no fetch tool available:
    DENY (url_fetch action, no capability) or
    REWRITE (plain-text analysis flag in payload)

Tests verify:
  - URL-only messages route to url_fetch action
  - url_fetch is DENIED by governor (no default capability)
  - URL-in-text messages rewrite payload with url_detected flag
  - URL-in-text still routes to chat (ALLOW)
  - Non-URL messages are unaffected
  - URL gate is deterministic
  - url_fetch can be explicitly granted (then ALLOW)
  - Various URL patterns detected (http, https, with paths)
"""
import pytest

from helensh.kernel import (
    init_session,
    step,
    replay,
    cognition,
    governor,
    grant_capability,
    KNOWN_ACTIONS,
    GATED_ACTIONS,
    DEFAULT_CAPABILITIES,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-url-gate-test")


# ── URL Detection in Cognition ────────────────────────────────────────


class TestURLDetection:
    def test_url_only_routes_to_url_fetch(self, s0):
        """A message that is just a URL → url_fetch action."""
        proposal = cognition(s0, "https://github.com/NousResearch/autoreason")
        assert proposal["action"] == "url_fetch"
        assert proposal["payload"]["url"] == "https://github.com/NousResearch/autoreason"

    def test_url_with_short_prefix_routes_to_url_fetch(self, s0):
        """URL with minimal text (< 10 chars non-URL) → url_fetch."""
        proposal = cognition(s0, "check https://example.com")
        assert proposal["action"] == "url_fetch"

    def test_url_in_longer_text_rewrites_chat(self, s0):
        """URL embedded in substantial text → chat with url_detected flag."""
        msg = "Please analyze the architecture described at https://example.com/paper and tell me your thoughts"
        proposal = cognition(s0, msg)
        assert proposal["action"] == "chat"
        assert proposal["payload"]["url_detected"] is True
        assert "https://example.com/paper" in proposal["payload"]["urls"]
        assert "[URL detected" in proposal["payload"]["message"]

    def test_no_url_unaffected(self, s0):
        """Regular messages without URLs are not affected."""
        proposal = cognition(s0, "hello world")
        assert proposal["action"] == "chat"
        assert "url_detected" not in proposal["payload"]

    def test_http_detected(self, s0):
        """http:// URLs are detected."""
        proposal = cognition(s0, "http://localhost:8780/health")
        assert proposal["action"] == "url_fetch"

    def test_https_detected(self, s0):
        """https:// URLs are detected."""
        proposal = cognition(s0, "https://arxiv.org/abs/2301.12345")
        assert proposal["action"] == "url_fetch"

    def test_prefixed_commands_not_affected(self, s0):
        """#read, #write, #task etc. with URLs are handled by their own routes."""
        proposal = cognition(s0, "#remember https://example.com is important")
        assert proposal["action"] == "memory_write"
        # URL detection only applies to chat fallback

    def test_multiple_urls_in_text(self, s0):
        """Multiple URLs in text → all captured in urls list."""
        msg = "Compare https://site-a.com and https://site-b.com for their different approaches to the problem"
        proposal = cognition(s0, msg)
        assert proposal["action"] == "chat"
        assert proposal["payload"]["url_detected"] is True
        assert len(proposal["payload"]["urls"]) == 2


# ── Governor Gate ─────────────────────────────────────────────────────


class TestURLGovernorGate:
    def test_url_fetch_in_known_actions(self):
        """url_fetch is a recognized action."""
        assert "url_fetch" in KNOWN_ACTIONS

    def test_url_fetch_in_gated_actions(self):
        """url_fetch is in the gated set (default disabled)."""
        assert "url_fetch" in GATED_ACTIONS

    def test_url_fetch_default_capability_false(self):
        """url_fetch capability is False by default."""
        assert DEFAULT_CAPABILITIES["url_fetch"] is False

    def test_url_fetch_denied_by_governor(self, s0):
        """url_fetch proposal is DENIED by governor (no capability)."""
        proposal = {"action": "url_fetch", "payload": {"url": "https://x.com"}, "authority": False}
        verdict = governor(proposal, s0)
        assert verdict == "DENY"

    def test_url_fetch_allowed_when_granted(self, s0):
        """url_fetch is ALLOWED if capability is explicitly granted."""
        s = grant_capability(s0, "url_fetch")
        proposal = {"action": "url_fetch", "payload": {"url": "https://x.com"}, "authority": False}
        verdict = governor(proposal, s)
        assert verdict == "ALLOW"


# ── Full Step Integration ─────────────────────────────────────────────


class TestURLStepIntegration:
    def test_url_only_step_denied(self, s0):
        """Full step with URL-only input → DENY verdict."""
        s, receipt = step(s0, "https://github.com/some/repo")
        assert receipt["verdict"] == "DENY"
        assert receipt["proposal"]["action"] == "url_fetch"

    def test_url_in_text_step_allowed(self, s0):
        """Full step with URL-in-text → ALLOW (rewritten chat)."""
        s, receipt = step(s0, "Tell me about the approach described at https://example.com/paper and how it compares to ours")
        assert receipt["verdict"] == "ALLOW"
        assert receipt["proposal"]["action"] == "chat"
        assert receipt["proposal"]["payload"]["url_detected"] is True

    def test_url_deny_no_state_mutation(self, s0):
        """DENIED url_fetch does not mutate state (I2 — NoSilentEffect)."""
        from helensh.state import effect_footprint
        fp_before = effect_footprint(s0)
        s, receipt = step(s0, "https://github.com/some/repo")
        assert receipt["verdict"] == "DENY"
        # env and capabilities should not change
        assert s["env"] == fp_before["env"]

    def test_url_deny_still_receipted(self, s0):
        """DENIED url_fetch still produces 2 receipts (I3)."""
        s, receipt = step(s0, "https://github.com/some/repo")
        assert len(s["receipts"]) == 2

    def test_url_gate_deterministic(self, s0):
        """Same URL input → same receipt hash (I1 — Determinism)."""
        s1, r1 = step(s0, "https://example.com")
        s2, r2 = step(s0, "https://example.com")
        assert r1["hash"] == r2["hash"]

    def test_url_gate_chain_integrity(self, s0):
        """URL gate steps maintain chain integrity (I4)."""
        s = replay(s0, [
            "hello",
            "https://example.com",  # DENIED
            "world",
        ])
        # Chain should have 6 receipts (3 steps × 2)
        assert len(s["receipts"]) == 6
        # Verify chain links
        from helensh.kernel import GENESIS_HASH
        assert s["receipts"][0]["previous_hash"] == GENESIS_HASH
        for i in range(1, len(s["receipts"])):
            assert s["receipts"][i]["previous_hash"] == s["receipts"][i - 1]["hash"]


# ── Edge Cases ────────────────────────────────────────────────────────


class TestURLEdgeCases:
    def test_empty_input_no_url(self, s0):
        """Empty input is not affected by URL gate."""
        proposal = cognition(s0, "")
        assert proposal["action"] == "chat"

    def test_url_like_but_not_url(self, s0):
        """Text that looks URL-ish but isn't (no http://) → normal chat."""
        proposal = cognition(s0, "check example.com for details")
        assert proposal["action"] == "chat"
        assert "url_detected" not in proposal["payload"]

    def test_ftp_not_gated(self, s0):
        """ftp:// URLs are not gated (only http/https)."""
        proposal = cognition(s0, "ftp://files.example.com/data.csv")
        assert proposal["action"] == "chat"
        assert "url_detected" not in proposal["payload"]
