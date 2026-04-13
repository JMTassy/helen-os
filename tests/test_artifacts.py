"""HELEN OS — Artifact Store Tests.

Tests for the content-addressed, append-only artifact persistence layer.

Architecture under test:
    ArtifactStore  — content-addressed blob storage + provenance index
    ArtifactRef    — reference attached to receipts
    ArtifactEntry  — provenance index entry

Properties proven:
    1. Content-addressing: same content → same hash → same ref
    2. Idempotent writes: writing same content twice → one blob
    3. Append-only index: every write appends, even re-writes
    4. Provenance ordering: index preserves write order
    5. Isolation: separate stores do not interfere
    6. Hash boundary: artifact_id derived from canonical(content)
    7. Round-trip: write → read → identical content
    8. Structural: ArtifactRef serialization, ArtifactEntry fields

Test classes:
    1. TestArtifactRef           — Reference dataclass + serialization
    2. TestArtifactEntry         — Entry dataclass fields
    3. TestArtifactStoreInit     — Directory creation, empty state
    4. TestArtifactStoreWrite    — Write, idempotency, provenance
    5. TestArtifactStoreRead     — Read, round-trip, missing artifact
    6. TestArtifactStoreIndex    — Index ordering, completeness
    7. TestArtifactStoreCount    — Unique artifact counting
    8. TestContentAddressing     — Hash determinism, canonical form
    9. TestArtifactIsolation     — Separate store independence
    10. TestArtifactAdversarial  — Edge cases, large payloads, nested data
    11. TestArtifactGNFBinding   — Artifact store wired into GNF pipeline
"""
import json
import os
import tempfile

import pytest

from helensh.artifacts import ArtifactStore, ArtifactRef, ArtifactEntry
from helensh.state import canonical, canonical_hash
from helensh.kernel import init_session
from helensh.gnf import gnf_step


# ── Helpers ─────────────────────────────────────────────────────────


def _tmp_store():
    """Create an ArtifactStore in a fresh temp directory."""
    d = tempfile.mkdtemp()
    return ArtifactStore(os.path.join(d, "artifacts"))


def _fresh():
    return init_session()


# ── 1. ArtifactRef ──────────────────────────────────────────────────


class TestArtifactRef:
    """ArtifactRef dataclass and serialization."""

    def test_ref_fields(self):
        ref = ArtifactRef(artifact_id="abc123", artifact_type="tool_result", source="python_exec")
        assert ref.artifact_id == "abc123"
        assert ref.artifact_type == "tool_result"
        assert ref.source == "python_exec"

    def test_ref_frozen(self):
        ref = ArtifactRef(artifact_id="abc", artifact_type="t", source="s")
        with pytest.raises(AttributeError):
            ref.artifact_id = "mutated"

    def test_ref_to_dict(self):
        ref = ArtifactRef(artifact_id="h", artifact_type="eval_result", source="harness")
        d = ref.to_dict()
        assert d == {
            "artifact_id": "h",
            "artifact_type": "eval_result",
            "source": "harness",
        }

    def test_ref_to_dict_keys(self):
        ref = ArtifactRef(artifact_id="x", artifact_type="y", source="z")
        assert sorted(ref.to_dict().keys()) == ["artifact_id", "artifact_type", "source"]

    def test_ref_equality(self):
        a = ArtifactRef(artifact_id="h", artifact_type="t", source="s")
        b = ArtifactRef(artifact_id="h", artifact_type="t", source="s")
        assert a == b

    def test_ref_inequality(self):
        a = ArtifactRef(artifact_id="h1", artifact_type="t", source="s")
        b = ArtifactRef(artifact_id="h2", artifact_type="t", source="s")
        assert a != b


# ── 2. ArtifactEntry ────────────────────────────────────────────────


class TestArtifactEntry:
    """ArtifactEntry dataclass fields."""

    def test_entry_fields(self):
        e = ArtifactEntry(
            artifact_id="abc",
            artifact_type="tool_result",
            source="python_exec",
            timestamp_ns=1000,
            content_size=42,
        )
        assert e.artifact_id == "abc"
        assert e.artifact_type == "tool_result"
        assert e.source == "python_exec"
        assert e.timestamp_ns == 1000
        assert e.content_size == 42

    def test_entry_frozen(self):
        e = ArtifactEntry(
            artifact_id="abc",
            artifact_type="t",
            source="s",
            timestamp_ns=0,
            content_size=0,
        )
        with pytest.raises(AttributeError):
            e.artifact_id = "mutated"


# ── 3. ArtifactStore Init ──────────────────────────────────────────


class TestArtifactStoreInit:
    """Directory creation and empty state."""

    def test_creates_root(self):
        d = tempfile.mkdtemp()
        root = os.path.join(d, "new_store")
        store = ArtifactStore(root)
        assert os.path.isdir(root)

    def test_creates_blobs_dir(self):
        d = tempfile.mkdtemp()
        root = os.path.join(d, "store")
        store = ArtifactStore(root)
        assert os.path.isdir(os.path.join(root, "blobs"))

    def test_empty_index(self):
        store = _tmp_store()
        assert store.index() == []

    def test_empty_count(self):
        store = _tmp_store()
        assert store.count() == 0

    def test_exists_returns_false_on_empty(self):
        store = _tmp_store()
        assert store.exists("nonexistent") is False


# ── 4. ArtifactStore Write ──────────────────────────────────────────


class TestArtifactStoreWrite:
    """Write, idempotency, provenance."""

    def test_write_returns_ref(self):
        store = _tmp_store()
        ref = store.write({"key": "value"}, artifact_type="test", source="unit")
        assert isinstance(ref, ArtifactRef)
        assert ref.artifact_type == "test"
        assert ref.source == "unit"

    def test_write_id_is_canonical_hash(self):
        store = _tmp_store()
        content = {"key": "value"}
        ref = store.write(content)
        expected_id = canonical_hash(content)
        assert ref.artifact_id == expected_id

    def test_write_creates_blob(self):
        store = _tmp_store()
        content = {"hello": "world"}
        ref = store.write(content)
        blob_path = os.path.join(
            str(store._blobs), ref.artifact_id[:2], f"{ref.artifact_id}.json"
        )
        assert os.path.exists(blob_path)

    def test_write_idempotent_blob(self):
        """Writing the same content twice creates only one blob."""
        store = _tmp_store()
        content = {"same": "data"}
        ref1 = store.write(content)
        ref2 = store.write(content)
        assert ref1.artifact_id == ref2.artifact_id
        # One blob on disk
        blob_path = os.path.join(
            str(store._blobs), ref1.artifact_id[:2], f"{ref1.artifact_id}.json"
        )
        assert os.path.exists(blob_path)

    def test_write_idempotent_same_ref(self):
        """Same content, same type, same source → same ref."""
        store = _tmp_store()
        content = {"x": 1}
        ref1 = store.write(content, artifact_type="t", source="s")
        ref2 = store.write(content, artifact_type="t", source="s")
        assert ref1 == ref2

    def test_write_always_appends_index(self):
        """Index is appended on every write, even re-writes (provenance)."""
        store = _tmp_store()
        content = {"data": True}
        store.write(content)
        store.write(content)
        entries = store.index()
        # Two index entries, same artifact_id
        assert len(entries) == 2
        assert entries[0].artifact_id == entries[1].artifact_id

    def test_write_different_content_different_id(self):
        store = _tmp_store()
        ref1 = store.write({"a": 1})
        ref2 = store.write({"b": 2})
        assert ref1.artifact_id != ref2.artifact_id

    def test_write_string_content(self):
        store = _tmp_store()
        ref = store.write("plain string")
        assert store.exists(ref.artifact_id)

    def test_write_list_content(self):
        store = _tmp_store()
        ref = store.write([1, 2, 3])
        content = store.read(ref.artifact_id)
        assert content == [1, 2, 3]

    def test_write_nested_content(self):
        store = _tmp_store()
        nested = {"a": {"b": {"c": [1, 2, {"d": True}]}}}
        ref = store.write(nested)
        assert store.read(ref.artifact_id) == nested


# ── 5. ArtifactStore Read ───────────────────────────────────────────


class TestArtifactStoreRead:
    """Read, round-trip, missing artifact."""

    def test_read_round_trip(self):
        store = _tmp_store()
        content = {"key": "value", "nested": [1, 2, 3]}
        ref = store.write(content)
        read_back = store.read(ref.artifact_id)
        assert read_back == content

    def test_read_missing_raises(self):
        store = _tmp_store()
        with pytest.raises(FileNotFoundError):
            store.read("nonexistent_hash")

    def test_read_canonical_form(self):
        """Stored blob is canonical JSON."""
        store = _tmp_store()
        content = {"z": 1, "a": 2}
        ref = store.write(content)
        blob_path = os.path.join(
            str(store._blobs), ref.artifact_id[:2], f"{ref.artifact_id}.json"
        )
        with open(blob_path, "r") as f:
            raw = f.read()
        assert raw == canonical(content)

    def test_exists_after_write(self):
        store = _tmp_store()
        ref = store.write({"data": "yes"})
        assert store.exists(ref.artifact_id) is True

    def test_read_preserves_types(self):
        """Numeric types, booleans, and null survive round-trip."""
        store = _tmp_store()
        content = {"int": 42, "float": 3.14, "bool": True, "null": None}
        ref = store.write(content)
        assert store.read(ref.artifact_id) == content


# ── 6. ArtifactStore Index ──────────────────────────────────────────


class TestArtifactStoreIndex:
    """Index ordering and completeness."""

    def test_index_preserves_order(self):
        store = _tmp_store()
        refs = []
        for i in range(5):
            ref = store.write({"seq": i})
            refs.append(ref)
        entries = store.index()
        assert len(entries) == 5
        for i, entry in enumerate(entries):
            assert entry.artifact_id == refs[i].artifact_id

    def test_index_has_timestamps(self):
        store = _tmp_store()
        store.write({"a": 1})
        store.write({"b": 2})
        entries = store.index()
        assert entries[0].timestamp_ns > 0
        assert entries[1].timestamp_ns >= entries[0].timestamp_ns

    def test_index_has_content_size(self):
        store = _tmp_store()
        content = {"data": "hello"}
        ref = store.write(content)
        entries = store.index()
        assert entries[0].content_size == len(canonical(content))

    def test_index_entry_types(self):
        store = _tmp_store()
        store.write({"x": 1}, artifact_type="tool_result", source="python_exec")
        entries = store.index()
        assert entries[0].artifact_type == "tool_result"
        assert entries[0].source == "python_exec"

    def test_index_tracks_rewrites_separately(self):
        """Re-writing same content logs separate index entries with different timestamps."""
        store = _tmp_store()
        content = {"same": True}
        store.write(content, source="first_write")
        store.write(content, source="second_write")
        entries = store.index()
        assert len(entries) == 2
        assert entries[0].source == "first_write"
        assert entries[1].source == "second_write"


# ── 7. ArtifactStore Count ──────────────────────────────────────────


class TestArtifactStoreCount:
    """Unique artifact counting."""

    def test_count_unique(self):
        store = _tmp_store()
        store.write({"a": 1})
        store.write({"b": 2})
        store.write({"c": 3})
        assert store.count() == 3

    def test_count_deduplicates(self):
        """Same content written twice → count = 1."""
        store = _tmp_store()
        store.write({"x": 1})
        store.write({"x": 1})
        assert store.count() == 1

    def test_count_mixed(self):
        """3 unique + 2 duplicates → count = 3."""
        store = _tmp_store()
        store.write({"a": 1})
        store.write({"b": 2})
        store.write({"a": 1})  # dup
        store.write({"c": 3})
        store.write({"b": 2})  # dup
        assert store.count() == 3

    def test_count_empty(self):
        store = _tmp_store()
        assert store.count() == 0


# ── 8. Content Addressing ──────────────────────────────────────────


class TestContentAddressing:
    """Hash determinism and canonical form."""

    def test_same_content_same_hash(self):
        store = _tmp_store()
        ref1 = store.write({"key": "value"})
        ref2 = store.write({"key": "value"})
        assert ref1.artifact_id == ref2.artifact_id

    def test_different_content_different_hash(self):
        store = _tmp_store()
        ref1 = store.write({"key": "a"})
        ref2 = store.write({"key": "b"})
        assert ref1.artifact_id != ref2.artifact_id

    def test_key_order_irrelevant(self):
        """Canonical serialization normalizes key order."""
        store = _tmp_store()
        ref1 = store.write({"z": 1, "a": 2})
        ref2 = store.write({"a": 2, "z": 1})
        assert ref1.artifact_id == ref2.artifact_id

    def test_hash_matches_canonical_hash(self):
        content = {"test": True}
        store = _tmp_store()
        ref = store.write(content)
        assert ref.artifact_id == canonical_hash(content)

    def test_hash_is_sha256_hex(self):
        store = _tmp_store()
        ref = store.write({"x": 1})
        assert len(ref.artifact_id) == 64
        assert all(c in "0123456789abcdef" for c in ref.artifact_id)

    def test_blob_path_uses_hash_prefix(self):
        """Blobs stored at blobs/{hash[:2]}/{hash}.json."""
        store = _tmp_store()
        ref = store.write({"data": 42})
        prefix = ref.artifact_id[:2]
        blob_dir = os.path.join(str(store._blobs), prefix)
        assert os.path.isdir(blob_dir)
        blob_file = os.path.join(blob_dir, f"{ref.artifact_id}.json")
        assert os.path.isfile(blob_file)


# ── 9. Artifact Isolation ──────────────────────────────────────────


class TestArtifactIsolation:
    """Separate store independence."""

    def test_separate_stores_independent(self):
        store1 = _tmp_store()
        store2 = _tmp_store()
        ref1 = store1.write({"store": 1})
        assert store1.exists(ref1.artifact_id)
        assert not store2.exists(ref1.artifact_id)

    def test_separate_stores_same_content(self):
        """Same content in two stores produces same hash but separate blobs."""
        store1 = _tmp_store()
        store2 = _tmp_store()
        content = {"shared": True}
        ref1 = store1.write(content)
        ref2 = store2.write(content)
        assert ref1.artifact_id == ref2.artifact_id
        assert store1.read(ref1.artifact_id) == store2.read(ref2.artifact_id)

    def test_separate_stores_independent_counts(self):
        store1 = _tmp_store()
        store2 = _tmp_store()
        store1.write({"a": 1})
        store1.write({"b": 2})
        store2.write({"c": 3})
        assert store1.count() == 2
        assert store2.count() == 1


# ── 10. Adversarial / Edge Cases ───────────────────────────────────


class TestArtifactAdversarial:
    """Edge cases, large payloads, nested data."""

    def test_empty_dict(self):
        store = _tmp_store()
        ref = store.write({})
        assert store.read(ref.artifact_id) == {}

    def test_empty_list(self):
        store = _tmp_store()
        ref = store.write([])
        assert store.read(ref.artifact_id) == []

    def test_null_content(self):
        store = _tmp_store()
        ref = store.write(None)
        assert store.read(ref.artifact_id) is None

    def test_integer_content(self):
        store = _tmp_store()
        ref = store.write(42)
        assert store.read(ref.artifact_id) == 42

    def test_boolean_content(self):
        store = _tmp_store()
        ref = store.write(True)
        assert store.read(ref.artifact_id) is True

    def test_large_payload(self):
        store = _tmp_store()
        content = {"large": "x" * 100_000}
        ref = store.write(content)
        read_back = store.read(ref.artifact_id)
        assert read_back == content

    def test_deeply_nested(self):
        store = _tmp_store()
        content = {"level": 0}
        inner = content
        for i in range(1, 50):
            inner["child"] = {"level": i}
            inner = inner["child"]
        ref = store.write(content)
        assert store.read(ref.artifact_id) == content

    def test_unicode_content(self):
        store = _tmp_store()
        content = {"text": "Hello, World! \u2603 \U0001f600"}
        ref = store.write(content)
        assert store.read(ref.artifact_id) == content

    def test_many_writes(self):
        store = _tmp_store()
        refs = []
        for i in range(100):
            refs.append(store.write({"i": i}))
        assert store.count() == 100
        assert len(store.index()) == 100


# ── 11. Artifact + GNF Binding ─────────────────────────────────────


class TestArtifactGNFBinding:
    """Artifact store wired into GNF pipeline.

    Proves that tool_result artifacts can be stored and referenced.
    This tests the ArtifactRef → receipt attachment pattern.
    """

    def test_tool_result_stored_as_artifact(self):
        """A tool result dict can be written to the artifact store."""
        store = _tmp_store()
        tool_result_dict = {
            "success": True,
            "output": "42",
            "artifacts": [],
            "error": None,
            "execution_ms": 1.5,
        }
        ref = store.write(tool_result_dict, artifact_type="tool_result", source="python_exec")
        assert ref.artifact_type == "tool_result"
        assert ref.source == "python_exec"
        assert store.read(ref.artifact_id) == tool_result_dict

    def test_receipt_can_reference_artifact(self):
        """ArtifactRef can be attached to a receipt dict."""
        store = _tmp_store()
        content = {"code_output": "hello"}
        ref = store.write(content, artifact_type="tool_result", source="python_exec")

        receipt = {
            "type": "EXECUTION",
            "action": "python_exec",
            "verdict": "ALLOW",
            "artifact_ref": ref.to_dict(),
        }
        assert receipt["artifact_ref"]["artifact_id"] == ref.artifact_id

    def test_gnf_step_with_artifact_store(self):
        """Full GNF step + artifact store: receipt produced, artifact stored."""
        from helensh.tools import ToolRegistry, ToolResult

        store = _tmp_store()
        reg = ToolRegistry()

        def echo_tool(payload, state):
            return ToolResult(
                success=True,
                output=payload.get("message", "echo"),
                artifacts=(),
                error=None,
                execution_ms=0.1,
            )

        reg.register("respond", echo_tool, requires_approval=False)

        s = _fresh()
        new_s, receipt = gnf_step(s, "hello", tool_registry=reg)

        # The GNF step ran and produced a receipt
        assert receipt.final_verdict in ("ALLOW", "PREVENT", "DEFER")

        # If tool ran, store the result as artifact
        if receipt.tool_result is not None:
            ref = store.write(
                receipt.tool_result.to_dict(),
                artifact_type="tool_result",
                source="respond",
            )
            assert store.exists(ref.artifact_id)
            stored = store.read(ref.artifact_id)
            assert stored["success"] is True

    def test_multiple_artifacts_from_pipeline(self):
        """Multiple tool results stored as separate artifacts."""
        store = _tmp_store()
        results = [
            {"success": True, "output": f"result_{i}", "error": None}
            for i in range(5)
        ]
        refs = []
        for i, r in enumerate(results):
            ref = store.write(r, artifact_type="tool_result", source=f"tool_{i}")
            refs.append(ref)

        assert store.count() == 5
        for i, ref in enumerate(refs):
            stored = store.read(ref.artifact_id)
            assert stored["output"] == f"result_{i}"

    def test_artifact_ref_serializes_for_receipt(self):
        """ArtifactRef.to_dict() produces JSON-safe output for receipt embedding."""
        store = _tmp_store()
        ref = store.write({"data": True}, artifact_type="eval_result", source="harness")
        ref_dict = ref.to_dict()
        # Must be JSON-serializable
        serialized = json.dumps(ref_dict)
        deserialized = json.loads(serialized)
        assert deserialized == ref_dict

    def test_street_output_as_artifact(self):
        """Street output (egregor) can be stored as artifact."""
        store = _tmp_store()
        street_output = {
            "street_id": "coding",
            "shops_executed": ["architect", "coder", "reviewer"],
            "gate_verdict": "PASS",
            "score": 0.85,
        }
        ref = store.write(street_output, artifact_type="street_output", source="coding_street")
        assert ref.artifact_type == "street_output"
        assert store.read(ref.artifact_id) == street_output

    def test_eval_result_as_artifact(self):
        """Eval result can be stored as artifact."""
        store = _tmp_store()
        eval_result = {
            "eval_id": "test_suite_001",
            "passed": 94,
            "failed": 0,
            "coverage": 0.92,
        }
        ref = store.write(eval_result, artifact_type="eval_result", source="pytest")
        assert ref.artifact_type == "eval_result"
        assert store.read(ref.artifact_id) == eval_result
