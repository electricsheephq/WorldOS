"""Campaign memory ledger — a searchable, per-campaign history index (P3.4).

This is the "recall" / LCM-grep layer: the DM and companions query it to stay
consistent across a long campaign ("what did we decide about Grett?", "who have
we met in the sump?"). It is a **strictly-derived index, NOT a source of truth**:
- authoritative state is the engine's snapshot.json + sessions/*.jsonl;
- this DB (`ledger.db`, beside the snapshot) makes that history full-text
  searchable via SQLite FTS5 (stdlib — no new dependency);
- it is rebuilt from those sources whenever they change (a tiny signature file
  detects staleness), so `recall` is provably a function of *committed* state and
  the ledger can never drift from the snapshot. There is no independent write
  path — nothing to keep in sync, nothing to corrupt.

Concurrency: the engine server, the viewer, and Tier-2 companion forks are
separate processes. Every connection opens WAL + a busy_timeout, and the rebuild
is idempotent (drop + repopulate), so concurrent reads/rebuilds are safe and the
ledger never touches the campaign_lock or the snapshot.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Optional

import store

KINDS = ("events", "dialogue", "decision", "npc_fact", "quest_milestone", "consequence", "lore")


def _db_path(campaign_id: str):
    return store._campaign_dir(campaign_id) / "ledger.db"


def _sig_path(campaign_id: str):
    return store._campaign_dir(campaign_id) / "ledger.sig"


def _connect(campaign_id: str) -> sqlite3.Connection:
    path = _db_path(campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS ledger USING fts5("
        "kind, who, text, ref UNINDEXED, day UNINDEXED, t UNINDEXED)"
    )
    return conn


def _signature(campaign_id: str) -> str:
    """A cheap fingerprint of the authoritative sources — changes whenever the
    snapshot or any session log changes, so we know when to rebuild."""
    d = store._campaign_dir(campaign_id)
    parts: list[str] = []
    snap = d / "snapshot.json"
    if snap.exists():
        st = snap.stat()
        parts.append(f"snap:{st.st_mtime_ns}:{st.st_size}")
    sessions = d / "sessions"
    if sessions.exists():
        for f in sorted(sessions.glob("*.jsonl")):
            parts.append(f"{f.name}:{f.stat().st_size}")
    return "|".join(parts)


def _ensure_fresh(campaign_id: str) -> None:
    """Rebuild the index from the snapshot + logs iff they've changed since the
    last build. Keeps `recall` a function of committed state with no write-through."""
    sig = _signature(campaign_id)
    sp = _sig_path(campaign_id)
    old = sp.read_text(encoding="utf-8") if sp.exists() else None
    if sig != old and sig:
        backfill(campaign_id)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(sig, encoding="utf-8")


def _safe_match(query: str) -> str:
    """Reduce an arbitrary query to a safe FTS5 OR-of-tokens, ranked by relevance.

    Each alnum token is wrapped as a quoted phrase and joined with OR, so a
    natural-language recall query ("what did we decide about the mill?") returns
    the memories matching the MOST terms first (bm25 rank) instead of nothing.
    Implicit-AND (space-joined) was the bug: it required EVERY word present, so
    real queries — which carry intent-words not in the stored text — matched
    zero rows. Quoting also neutralizes FTS5 operators/punctuation (injection-safe)."""
    toks = re.findall(r"[A-Za-z0-9]+", query or "")
    return " OR ".join(f'"{t}"' for t in toks)


def _row(r) -> dict:
    return {"kind": r[0], "who": r[1], "text": r[2], "ref": r[3], "day": r[4]}


def recall(campaign_id: str, query: str, kinds: Optional[list] = None, limit: int = 8) -> list[dict]:
    """Full-text search the campaign's history, ranked by relevance. Read-only from
    the caller's view (it may rebuild a stale index first). `kinds` optionally
    filters to a subset of KINDS. Returns [{kind, who, text, ref, day}]."""
    match = _safe_match(query)
    if not match:
        return []
    _ensure_fresh(campaign_id)
    if not _db_path(campaign_id).exists():
        return []
    conn = _connect(campaign_id)
    # Push the `kinds` filter into SQL BEFORE the rank/limit — filtering in Python after a
    # global `LIMIT n*5` lets a flood of matching session events starve a rare lore/decision
    # row exactly when a campaign gets long (the bug). With the constraint in the query, the
    # ranked top-N is computed within the requested kinds. (#48)
    sql = "SELECT kind, who, text, ref, day FROM ledger WHERE ledger MATCH ?"
    params: list = [match]
    if kinds:
        sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
        params.extend(kinds)
    sql += " ORDER BY rank LIMIT ?"
    params.append(max(limit, 1))
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [_row(r) for r in rows]


def recall_npc(campaign_id: str, npc_id: str, limit: int = 12) -> list[dict]:
    """Everything recorded about / said by one character (by `who`). Read-only."""
    _ensure_fresh(campaign_id)
    if not _db_path(campaign_id).exists():
        return []
    conn = _connect(campaign_id)
    try:
        rows = conn.execute(
            "SELECT kind, who, text, ref, day FROM ledger WHERE who = ? ORDER BY t DESC LIMIT ?",
            (npc_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def recall_decisions(campaign_id: str, query: str = "", limit: int = 12) -> list[dict]:
    """Recorded party decisions, optionally filtered by a text query. Read-only."""
    if query.strip():
        return recall(campaign_id, query, kinds=["decision"], limit=limit)
    _ensure_fresh(campaign_id)
    if not _db_path(campaign_id).exists():
        return []
    conn = _connect(campaign_id)
    try:
        rows = conn.execute(
            "SELECT kind, who, text, ref, day FROM ledger WHERE kind='decision' "
            "ORDER BY t DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def backfill(campaign_id: str) -> int:
    """Rebuild the entire ledger from the authoritative snapshot + session logs
    (drop + repopulate — the ledger is a derived index). Returns records indexed."""
    campaign = store.load_campaign(campaign_id)
    if campaign is None:
        return 0
    conn = _connect(campaign_id)
    n = 0
    try:
        conn.execute("DROP TABLE IF EXISTS ledger")
        conn.execute(
            "CREATE VIRTUAL TABLE ledger USING fts5("
            "kind, who, text, ref UNINDEXED, day UNINDEXED, t UNINDEXED)"
        )

        def _ins(kind, text, who="", ref="", day=0):
            nonlocal n
            if text:
                conn.execute(
                    "INSERT INTO ledger(kind, who, text, ref, day, t) VALUES (?,?,?,?,?,?)",
                    (kind, who, text, ref, day, time.time()),
                )
                n += 1

        for sid in campaign.session_ids:
            for e in store.read_log(campaign_id, sid):
                if e.kind in ("narration", "dialogue", "combat", "system"):
                    _ins("dialogue" if e.kind == "dialogue" else "events", e.text, who=e.speaker or "")
        for ch in campaign.characters.values():
            for fact in ch.memory:
                _ins("npc_fact", fact, who=ch.id, ref=ch.id)
        for d in campaign.decisions:
            _ins("decision", " | ".join(filter(None, [d.summary, d.chosen, d.rationale])), ref=d.id, day=d.day)
        for cq in campaign.consequences:
            _ins("consequence", cq.text, ref=cq.id, day=cq.trigger_day)
        for q in campaign.quests.values():
            _ins("quest_milestone", f"{q.title} [{q.status}]", ref=q.id)
        for fact in getattr(campaign, "lore", []):  # world-bible facts -> recallable
            _ins("lore", fact)
        conn.commit()
    finally:
        conn.close()
    return n
