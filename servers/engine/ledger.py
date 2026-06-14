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

import hashlib
import re
import sqlite3
import time
from typing import Optional

import store
from wrapper_progress import is_wrapper_progress_line

KINDS = ("events", "dialogue", "decision", "npc_fact", "quest_milestone", "consequence", "lore")

# F07-1 (issue #772): combat/system BOOKKEEPING must not enter the FTS index and
# outrank story in recall (a recall('Rolan') probe returned 4 of 6 top hits as
# bookkeeping). Two sources of contamination, decontaminated by EXACT discipline so the
# documented DM-system-note path (SKILL.md:47 — a terse DM-authored kind=system note IS
# meant to feed recall) is preserved:
#   1. engine combat-event rows, stamped this schema in payload by _log_combat_event;
#   2. the engine's two session markers ("Session N began" / "Session ended."), written
#      by start_session/end_session — matched by exact prefix (same exact-match
#      discipline as #749's wrapper-line filter), so a DM note that merely mentions a
#      session is still indexed.
_COMBAT_EVENT_SCHEMA = "clawdnd.combat_event.v1"
# Anchored to the engine's own marker text; \b/(:|$) so a DM prose row that starts with
# the same words but continues differently is NOT swallowed.
_SESSION_MARKER_RE = re.compile(r"^(Session \d+ began\b|Session ended\.)")


def _is_combat_event(e) -> bool:
    """True iff a session-log entry is an engine combat-event bookkeeping row."""
    if e.kind != "combat":
        return False
    payload = getattr(e, "payload", None)
    return isinstance(payload, dict) and payload.get("schema") == _COMBAT_EVENT_SCHEMA


def _is_session_marker(e) -> bool:
    """True iff a kind=system row is one of the engine's two session markers."""
    return e.kind == "system" and bool(_SESSION_MARKER_RE.match(e.text or ""))


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


def _snapshot_projection_digest(campaign_id: str) -> str:
    """F07-8 (issue #803): a CONTENT digest of exactly the snapshot fields the
    backfill indexes (character memory facts, decisions, consequences, quests,
    lore). The old signature keyed the snapshot by mtime:size, so EVERY state save
    (HP, clock, arc progress, currency) flipped it and forced a full DROP+reparse
    even though none of the indexed projection changed — the genuinely-false-positive
    invalidations the audit names.

    Digesting CONTENT (not lengths/counts) is load-bearing: a forget(Y)+remember(X)
    pair restores the same len(ch.memory) and an ending-overlay lore de-confliction
    can REPLACE lore items without changing count, so a length digest would keep a
    stale index. We hash the exact strings backfill reads — a small projection, cheap
    to materialize — so the digest changes iff the indexed memory changes. Missing
    snapshot -> "" (no projection), same as before."""
    campaign = store.load_campaign(campaign_id)
    if campaign is None:
        return ""
    h = hashlib.sha256()

    def feed(*vals) -> None:
        for v in vals:
            h.update(b"\x1f")
            h.update(str(v if v is not None else "").encode("utf-8", "replace"))
        h.update(b"\x1e")

    # Order mirrors backfill so the digest is a faithful fingerprint of what is indexed.
    for ch in campaign.characters.values():
        for fact in ch.memory:
            feed("npc_fact", ch.id, fact)
    for d in campaign.decisions:
        feed("decision", d.id, d.day, d.summary, d.chosen, d.rationale)
    for cq in campaign.consequences:
        feed("consequence", cq.id, cq.trigger_day, cq.text)
    for q in campaign.quests.values():
        feed("quest", q.id, q.title, q.status)
    for fact in getattr(campaign, "lore", []):
        feed("lore", fact)
    return h.hexdigest()


def _signature(campaign_id: str) -> str:
    """A fingerprint of the authoritative sources that changes whenever the indexed
    memory changes, so we know when to rebuild. The snapshot term is a CONTENT digest
    of the indexed projection (F07-8) — NOT the snapshot's mtime/size — so a pure-state
    save (HP/clock/currency, none of it indexed) is no longer a false-positive
    invalidation. Session logs are append-only and the new row legitimately needs
    indexing, so they stay keyed by byte size (a grown log == new memory to index)."""
    d = store._campaign_dir(campaign_id)
    parts: list[str] = []
    snap = d / "snapshot.json"
    if snap.exists():
        parts.append("snap:" + _snapshot_projection_digest(campaign_id))
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


def _resolve_npc_keys(campaign_id: str, npc_id: str) -> tuple[list[str], Optional[str]]:
    """SYN-10 (F07-2 + F10-5): resolve a recall_npc argument to ALL of the stable
    keys it could be indexed under, so a query by id OR by name returns BOTH the
    facts (indexed who=ch.id) AND the dialogue (indexed who=ch.name).

    The ledger has a split-brain: backfill writes who=ch.id for npc_facts but
    who=speaker for dialogue, and the engine logs dialogue with speaker=ch.name
    (server.py:4000/4411/4451/4644/5290/...). models.py documents speaker as
    "character id or name", so the argument itself is ambiguous too.

    Read-only against the snapshot (load_campaign, never _require/save): match the
    argument against the roster by id OR casefolded name. On a hit, return both the
    canonical id and name as keys, plus the canonical id to match the dialogue
    `ref` belt that backfill stamps. On a MISS (an ad-hoc free-text speaker that is
    not a roster character) return just the argument — single-key behavior, so
    free-text speakers are unaffected and the resolution never invents a
    cross-match (Guard 2)."""
    arg = (npc_id or "").strip()
    keys: list[str] = [arg] if arg else []
    ref: Optional[str] = None
    try:
        campaign = store.load_campaign(campaign_id)
    except Exception:
        campaign = None
    if campaign is not None and arg:
        folded = arg.casefold()
        for ch in campaign.characters.values():
            if ch.id == arg or (ch.name or "").casefold() == folded:
                for k in (ch.id, ch.name):
                    if k and k not in keys:
                        keys.append(k)
                ref = ch.id
                break
    return keys, ref


def recall_npc(campaign_id: str, npc_id: str, limit: int = 12) -> list[dict]:
    """Everything recorded about / said by one character — facts, dialogue,
    consequences — retrievable by EITHER the character's id OR name (SYN-10).
    Read-only (it may rebuild a stale index first; never writes state)."""
    keys, ref = _resolve_npc_keys(campaign_id, npc_id)
    if not keys:
        return []
    _ensure_fresh(campaign_id)
    if not _db_path(campaign_id).exists():
        return []
    # Match any of the resolved keys (id and/or name) case-insensitively, OR the
    # `ref` belt (the canonical id backfill stamps onto a roster speaker's dialogue
    # rows) — so dialogue (who=name) and facts (who=id, ref=id) both come back no
    # matter which stable key the caller passed.
    where = " OR ".join("who = ? COLLATE NOCASE" for _ in keys)
    params: list = list(keys)
    if ref:
        where += " OR ref = ?"
        params.append(ref)
    params.append(limit)
    conn = _connect(campaign_id)
    try:
        rows = conn.execute(
            f"SELECT kind, who, text, ref, day FROM ledger WHERE {where} ORDER BY t DESC LIMIT ?",
            params,
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

        # SYN-10 belt: a speaker string -> canonical character id, keyed by BOTH the
        # id and the casefolded name (the engine logs dialogue with speaker=ch.name).
        # When a dialogue row's speaker resolves to a roster character we stamp the
        # row's ref=<id>, so recall_npc(id) finds the dialogue via the `ref` belt even
        # before query-time resolution and the two keys stay in lock-step. A free-text
        # speaker (no roster hit) gets ref="" and is reachable only by its exact key.
        speaker_to_id: dict[str, str] = {}
        for ch in campaign.characters.values():
            speaker_to_id[ch.id] = ch.id
            if ch.name:
                speaker_to_id.setdefault(ch.name.casefold(), ch.id)

        for sid in campaign.session_ids:
            for e in store.read_log(campaign_id, sid):
                if e.kind in ("narration", "dialogue", "combat", "system"):
                    # #749: never index the wrapper progress heartbeat — it is mid-turn
                    # liveness filler, not campaign memory; recall must never surface it.
                    if is_wrapper_progress_line(e.text):
                        continue
                    # F07-1 (#772): combat-event rows and the engine's session markers
                    # are bookkeeping, not memory — skip them so recall ranks story, not
                    # "Tough 1 takes 5 force damage" / "Session 2 began". A DM-authored
                    # kind=system note (non-marker) still falls through and stays indexed
                    # (SKILL.md:47 contract).
                    if _is_combat_event(e) or _is_session_marker(e):
                        continue
                    speaker = e.speaker or ""
                    ref = speaker_to_id.get(speaker) or speaker_to_id.get(speaker.casefold(), "")
                    _ins(
                        "dialogue" if e.kind == "dialogue" else "events",
                        e.text, who=speaker, ref=ref,
                    )
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
