"""
HELEN OS Memory Spine — Constitutional Persistent Store

Storage: SQLite (stdlib, zero dependencies, local-first)
Contract:
  - Reads are non-sovereign (authority=NONE)
  - Writes require reducer authorization (actor=MAYOR|SYSTEM)
  - mutation_log is append-only with chained hashes (I1, I8)
  - Deterministic retrieval: ORDER BY id + pure scoring = same input, same output
"""

import sqlite3
import json
import hashlib
import os
from datetime import datetime, timezone


DB_PATH = os.environ.get("HELEN_MEMORY_DB", os.path.join(os.path.dirname(__file__), "..", "helen_memory.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus (
    id              TEXT PRIMARY KEY,
    object_type     TEXT NOT NULL,
    title           TEXT NOT NULL,
    district        TEXT NOT NULL,
    relevance       TEXT NOT NULL,
    authority_class TEXT NOT NULL,
    status          TEXT NOT NULL,
    priority        TEXT NOT NULL,
    salience_now    TEXT NOT NULL,
    helen_stance    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    superseded_by   TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    provider    TEXT,
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mutation_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    action      TEXT NOT NULL,
    corpus_id   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    authority   TEXT NOT NULL,
    payload     TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
"""

# Salience & Stance weights (for ranking)
SALIENCE_W = {"core_now": 3, "active_supporting": 2, "watchlist": 1, "dormant": 0, "archive": -1}
PRIORITY_W = {"critical": 3, "high": 2, "medium": 1, "low": 0}
STANCE_W = {"deep_helen_interest": 2, "moderate_interest": 1, "low_interest": 0, "utility_only": -1}

VALID_ACTORS = {"MAYOR", "SYSTEM"}
VALID_ACTIONS = {"INSERT", "UPDATE_SALIENCE", "SUPERSEDE"}


def _connect():
    return sqlite3.connect(DB_PATH)


def init_db():
    """Initialize the database schema. Idempotent."""
    conn = _connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _chain_hash(seq, action, corpus_id, payload, prev_hash):
    """Compute chained hash for mutation log entry."""
    data = f"{seq}|{action}|{corpus_id}|{payload}|{prev_hash}"
    return hashlib.sha256(data.encode()).hexdigest()


def _last_hash(conn):
    """Get the hash of the most recent mutation log entry."""
    row = conn.execute("SELECT hash FROM mutation_log ORDER BY seq DESC LIMIT 1").fetchone()
    return row[0] if row else "0" * 64


def score_object(obj):
    """Score a corpus object by salience + priority + stance."""
    return (
        SALIENCE_W.get(obj.get("salience_now", ""), 0)
        + PRIORITY_W.get(obj.get("priority", ""), 0)
        + STANCE_W.get(obj.get("helen_stance", ""), 0)
    )


def load_corpus():
    """
    Load active corpus objects from SQLite.
    authority=NONE — non-sovereign retrieval.
    Returns list of dicts, deterministic order.
    """
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM corpus WHERE superseded_by IS NULL ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def seed_corpus(registry):
    """
    Seed the corpus from a static registry list (one-time migration).
    Skips entries that already exist.
    """
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    prev_hash = _last_hash(conn)

    for obj in registry:
        existing = conn.execute("SELECT id FROM corpus WHERE id = ?", (obj["id"],)).fetchone()
        if existing:
            continue

        conn.execute(
            """INSERT INTO corpus
               (id, object_type, title, district, relevance, authority_class,
                status, priority, salience_now, helen_stance, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (obj["id"], obj["object_type"], obj["title"], obj["district"],
             obj["relevance"], obj["authority_class"], obj["status"],
             obj["priority"], obj["salience_now"], obj["helen_stance"],
             now, now),
        )

        # Log the mutation
        payload = json.dumps(obj, sort_keys=True)
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM mutation_log").fetchone()[0]
        h = _chain_hash(seq, "INSERT", obj["id"], payload, prev_hash)
        conn.execute(
            """INSERT INTO mutation_log
               (timestamp, action, corpus_id, actor, authority, payload, prev_hash, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, "INSERT", obj["id"], "SYSTEM", "reducer", payload, prev_hash, h),
        )
        prev_hash = h

    conn.commit()
    conn.close()


def mutate_corpus(action, corpus_id, payload_dict, actor):
    """
    Mutate the corpus. Reducer-gated.
    Returns the mutation log entry or raises ValueError.
    """
    if actor not in VALID_ACTORS:
        raise ValueError(f"Unauthorized actor: {actor}. Only {VALID_ACTORS} may mutate.")
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action}. Must be one of {VALID_ACTIONS}.")

    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    prev_hash = _last_hash(conn)
    payload = json.dumps(payload_dict, sort_keys=True)

    if action == "INSERT":
        required = {"object_type", "title", "district", "relevance",
                     "authority_class", "status", "priority", "salience_now", "helen_stance"}
        missing = required - set(payload_dict.keys())
        if missing:
            conn.close()
            raise ValueError(f"Missing required fields for INSERT: {missing}")

        conn.execute(
            """INSERT INTO corpus
               (id, object_type, title, district, relevance, authority_class,
                status, priority, salience_now, helen_stance, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (corpus_id, payload_dict["object_type"], payload_dict["title"],
             payload_dict["district"], payload_dict["relevance"],
             payload_dict["authority_class"], payload_dict["status"],
             payload_dict["priority"], payload_dict["salience_now"],
             payload_dict["helen_stance"], now, now),
        )

    elif action == "UPDATE_SALIENCE":
        updates = {}
        for field in ("salience_now", "helen_stance", "priority", "status"):
            if field in payload_dict:
                updates[field] = payload_dict[field]
        if not updates:
            conn.close()
            raise ValueError("UPDATE_SALIENCE requires at least one of: salience_now, helen_stance, priority, status")

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [now, corpus_id]
        conn.execute(f"UPDATE corpus SET {set_clause}, updated_at = ? WHERE id = ?", values)

    elif action == "SUPERSEDE":
        new_id = payload_dict.get("new_id")
        if not new_id:
            conn.close()
            raise ValueError("SUPERSEDE requires 'new_id' in payload")
        conn.execute("UPDATE corpus SET superseded_by = ?, updated_at = ? WHERE id = ?",
                      (new_id, now, corpus_id))

    # Log the mutation
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM mutation_log").fetchone()[0]
    h = _chain_hash(seq, action, corpus_id, payload, prev_hash)
    conn.execute(
        """INSERT INTO mutation_log
           (timestamp, action, corpus_id, actor, authority, payload, prev_hash, hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, action, corpus_id, actor, "reducer", payload, prev_hash, h),
    )

    conn.commit()
    entry = {"seq": seq, "action": action, "corpus_id": corpus_id,
             "actor": actor, "hash": h, "timestamp": now}
    conn.close()
    return entry


def get_mutation_log(limit=100):
    """Read-only access to mutation log. authority=NONE."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM mutation_log ORDER BY seq DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def corpus_count():
    """Count active corpus objects."""
    conn = _connect()
    count = conn.execute("SELECT COUNT(*) FROM corpus WHERE superseded_by IS NULL").fetchone()[0]
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Conversation Memory — non-sovereign retrieval (authority=NONE)
# Lightweight session-based message history for /chat continuity.
# ---------------------------------------------------------------------------


def save_exchange(session_id, user_msg, assistant_msg, provider=None):
    """
    Persist a user/assistant exchange to the conversations table.
    Both messages share the same session_id and timestamp batch.
    authority=NONE — conversation memory is non-sovereign retrieval.
    """
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO conversations (session_id, role, content, provider, timestamp) VALUES (?, ?, ?, ?, ?)",
        (session_id, "user", user_msg, provider, now),
    )
    conn.execute(
        "INSERT INTO conversations (session_id, role, content, provider, timestamp) VALUES (?, ?, ?, ?, ?)",
        (session_id, "assistant", assistant_msg, provider, now),
    )
    conn.commit()
    conn.close()


def get_recent_history(session_id, limit=10):
    """
    Return the most recent messages for a session, ordered chronologically.
    Each entry is a dict with keys: role, content, provider, timestamp.
    Limit applies to *exchanges* (pairs), so up to limit*2 rows are returned.
    authority=NONE — non-sovereign retrieval.
    """
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT role, content, provider, timestamp
           FROM conversations
           WHERE session_id = ?
           ORDER BY id DESC
           LIMIT ?""",
        (session_id, limit * 2),
    ).fetchall()
    conn.close()
    # Reverse so oldest-first (chronological order for prompt assembly)
    return [dict(r) for r in reversed(rows)]


def get_last_session_summary():
    """
    Return the most recent session_id and its message count,
    or None if no conversations exist.
    authority=NONE — non-sovereign retrieval.
    """
    conn = _connect()
    row = conn.execute(
        """SELECT session_id, COUNT(*) as message_count
           FROM conversations
           GROUP BY session_id
           ORDER BY MAX(id) DESC
           LIMIT 1"""
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"session_id": row[0], "message_count": row[1]}
