"""HELEN OS — Content-Addressed Artifact Store.

Every tool execution, street output, and eval result
produces an artifact that is persisted immutably.

    Receipt = governance witness (what was decided)
    Artifact = execution witness (what was produced)

These are separate. Receipts reference artifacts by hash.
Artifacts are never in receipt_hash (same boundary as trace).

Storage layout:
    {root}/
        index.jsonl              — append-only provenance index
        blobs/
            {hash[:2]}/
                {hash}.json      — content-addressed blob

Rules:
    1. Content-addressed: id = sha256(canonical(content))
    2. Append-only: artifacts are never deleted or modified
    3. Idempotent: writing the same content twice is a no-op
    4. Index is append-only provenance log
    5. Blobs are immutable once written
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from helensh.state import canonical, canonical_hash


# ── Types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to a stored artifact. Attached to receipts."""
    artifact_id: str       # sha256 hash
    artifact_type: str     # "tool_result" | "street_output" | "eval_result"
    source: str            # what produced it: tool name, street id, etc.

    def to_dict(self) -> Dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "source": self.source,
        }


@dataclass(frozen=True)
class ArtifactEntry:
    """Index entry — provenance metadata for one artifact."""
    artifact_id: str
    artifact_type: str
    source: str
    timestamp_ns: int
    content_size: int


# ── Store ───────────────────────────────────────────────────────────


class ArtifactStore:
    """Content-addressed, append-only artifact persistence.

    Usage:
        store = ArtifactStore("/path/to/artifacts")
        ref = store.write(tool_result.to_dict(), artifact_type="tool_result", source="python_exec")
        content = store.read(ref.artifact_id)
        assert store.exists(ref.artifact_id)
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._blobs = self.root / "blobs"
        self._index_path = self.root / "index.jsonl"
        # Create directories
        self.root.mkdir(parents=True, exist_ok=True)
        self._blobs.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        content: Any,
        artifact_type: str = "unknown",
        source: str = "unknown",
    ) -> ArtifactRef:
        """Write content to the store. Returns an ArtifactRef.

        Idempotent: writing the same content twice returns the same ref
        without duplicating the blob (content-addressed).
        """
        # Serialize
        content_str = canonical(content)
        artifact_id = canonical_hash(content)

        # Write blob (idempotent)
        blob_dir = self._blobs / artifact_id[:2]
        blob_dir.mkdir(parents=True, exist_ok=True)
        blob_path = blob_dir / f"{artifact_id}.json"

        if not blob_path.exists():
            blob_path.write_text(content_str, encoding="utf-8")

        # Append to index (always — even on re-write, for provenance)
        entry = {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "source": source,
            "timestamp_ns": time.monotonic_ns(),
            "content_size": len(content_str),
        }
        with self._index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

        return ArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            source=source,
        )

    def read(self, artifact_id: str) -> Any:
        """Read artifact content by ID. Returns deserialized content.

        Raises FileNotFoundError if artifact does not exist.
        """
        blob_path = self._blobs / artifact_id[:2] / f"{artifact_id}.json"
        if not blob_path.exists():
            raise FileNotFoundError(f"artifact not found: {artifact_id}")
        content_str = blob_path.read_text(encoding="utf-8")
        return json.loads(content_str)

    def exists(self, artifact_id: str) -> bool:
        """Check if an artifact exists in the store."""
        blob_path = self._blobs / artifact_id[:2] / f"{artifact_id}.json"
        return blob_path.exists()

    def index(self) -> List[ArtifactEntry]:
        """Read the full provenance index."""
        if not self._index_path.exists():
            return []
        entries = []
        with self._index_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    entries.append(ArtifactEntry(
                        artifact_id=d["artifact_id"],
                        artifact_type=d["artifact_type"],
                        source=d["source"],
                        timestamp_ns=d["timestamp_ns"],
                        content_size=d["content_size"],
                    ))
        return entries

    def count(self) -> int:
        """Number of unique artifacts in the store."""
        seen = set()
        if not self._index_path.exists():
            return 0
        with self._index_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    seen.add(d["artifact_id"])
        return len(seen)


__all__ = [
    "ArtifactStore",
    "ArtifactRef",
    "ArtifactEntry",
]
