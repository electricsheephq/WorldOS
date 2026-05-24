"""World lore retrieval — the DM's on-demand "wiki" for a universe seed.

A world seed may ship a corpus of lore pages under
``content/worlds/<id>/lore/*.md`` (authored, or ingested from a wiki). This module
makes that corpus searchable so the DM can pull canon on demand — "the party reaches
Wyrm's Crossing" -> ``lookup_lore("baldurs-gate", "Wyrm's Crossing")`` -> the relevant
pages — and ground its generation in established lore (and the world's chronology)
instead of inventing canon from scratch.

Design (mirrors the rules lookup + the ledger's FTS approach, kept dependency-free):
- Pure module, stdlib only (SQLite FTS5 in-memory). No campaign state, no MCP here.
- A "page" is one ``*.md`` file: its title is the first ``# heading`` (or the file
  name); the body is the rest. Pages may carry an optional ``era:`` / ``year:`` line
  the DM uses for chronology.
- ``lookup_lore`` builds an in-memory FTS5 index over the corpus and OR-ranks pages by
  relevance (same fix as recall: OR-of-tokens, not implicit-AND), returning bounded
  excerpts so the DM gets canon without dumping a whole wiki into context.

The corpus is content (CC-BY-4.0 original, or CC-BY-SA wiki-derived fan content); this
code is MIT and reads it generically — it never hard-codes any setting.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Optional


def _content_dir() -> Path:
    raw = os.environ.get("CLAWDND_CONTENT_DIR")
    return Path(raw).expanduser() if raw else Path(__file__).resolve().parents[2] / "content"


def _lore_dir(world_id: str) -> Optional[Path]:
    """content/worlds/<id>/lore/, falling back to the gitignored _private/ seed."""
    base = _content_dir() / "worlds"
    for cand in (base / world_id / "lore", base / "_private" / world_id / "lore"):
        if cand.is_dir():
            return cand
    return None


def _title_and_body(text: str, fallback: str) -> tuple[str, str]:
    body = text.strip()
    m = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    title = m.group(1).strip() if m else fallback
    return title, body


def _page_era(text: str) -> str:
    """A page's chronology line, if it declares one (`*Era: ...*` / `era: ...` near the
    top). Authored canon pages assert the world's era; ingested wiki pages usually don't."""
    m = re.search(r"(?im)^\*?\s*(?:era|status|year)\s*:\s*(.+?)\*?\s*$", text)
    return m.group(1).strip() if m else ""


def _pages(world_id: str) -> list[dict]:
    """Every lore page: {title, text, source, tier, era}. tier 0 = authored (lore/*.md),
    tier 1 = ingested (lore/wiki/*.md) — authored canon outranks ingested in retrieval."""
    d = _lore_dir(world_id)
    if d is None:
        return []
    out: list[dict] = []
    # Recursive: authored pages live at lore/*.md; ingested wiki pages at lore/wiki/*.md.
    for f in sorted(d.rglob("*.md")):
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if f.name.upper() in ("LICENSE.MD", "README.MD", "ATTRIBUTION.MD"):
            continue
        title, body = _title_and_body(raw, f.stem.replace("-", " ").title())
        if body:
            tier = 0 if f.parent == d else 1  # top-level authored beats any subfolder (wiki)
            out.append({"title": title, "text": body, "source": f.name, "tier": tier, "era": _page_era(body)})
    return out


def has_corpus(world_id: str) -> bool:
    return bool(_pages(world_id))


def _safe_match(query: str) -> str:
    """OR-of-quoted-tokens (relevance, not all-terms-required) — same fix as recall."""
    toks = re.findall(r"[A-Za-z0-9]+", query or "")
    return " OR ".join(f'"{t}"' for t in toks)


def _excerpt(text: str, tokens: list[str], width: int = 600) -> str:
    """A bounded snippet centered on the first query-term hit (so the DM gets the
    relevant passage, not a whole page). Falls back to the page head."""
    flat = " ".join(text.split())
    if tokens:
        low = flat.lower()
        pos = min((low.find(t.lower()) for t in tokens if t.lower() in low), default=-1)
        if pos > 0:
            start = max(0, pos - width // 3)
            seg = flat[start:start + width]
            return ("…" if start else "") + seg + ("…" if start + width < len(flat) else "")
    return flat[:width] + ("…" if len(flat) > width else "")


def lookup_lore(
    world_id: str,
    query: str,
    limit: int = 5,
    *,
    supersedes: Optional[list[str]] = None,
    canon_header: str = "",
) -> list[dict]:
    """Search a world's lore corpus and return the most relevant pages, each as
    {title, excerpt, source, era}. **Authored canon (tier 0) outranks ingested wiki
    pages (tier 1)** among matches, so the seed's intended (e.g. post-canon) truth wins
    over longer-but-stale wiki pages on a bm25 tie. Empty if no corpus / no match.
    Read-only; builds a throwaway in-memory index per call.

    De-confliction (additive — both args default to the no-op behavior):
    - `supersedes`: case-insensitive substrings the active ending RETRACTS (the same
      predicate `_apply_ending_overlay` applies to `c.lore`). A retrieved excerpt that
      asserts a superseded fact (e.g. "Gortash is dead" under the tyranny ending) is
      DROPPED whenever any non-contradicting hit exists to take its place, and kept
      (DEMOTED to the end) only when the entire match set is superseded — so the
      contradiction never LEADS the result and never out-ranks the corrected canon, yet a
      query whose only matches are superseded still returns *something*. This closes the
      two-surface bug structurally (the .md corpus is de-conflicted on the same basis as
      c.lore), reusing the tier machinery's "the right canon wins the top slot" guarantee.
    - `canon_header`: when non-empty, prepended as a synthetic hit (source
      "world-state") carrying the campaign's authoritative world-state — so the DM's
      ground truth for the scene is the structured row, with the pages below framed as
      background that may describe other timelines. A *belt* over the demotion's
      *suspenders*.

    With both unset/empty the output is byte-identical to before."""
    pages = _pages(world_id)
    match = _safe_match(query)
    if not pages or not match:
        return []
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE lore USING fts5(title, body, src UNINDEXED, tier UNINDEXED)")
        conn.executemany(
            "INSERT INTO lore(rowid, title, body, src, tier) VALUES (?,?,?,?,?)",
            [(i, p["title"], p["text"], p["source"], p.get("tier", 1)) for i, p in enumerate(pages)],
        )

        def _match_tier(tier: int, n: int) -> list[int]:
            # Filter on the UNINDEXED tier column alongside MATCH so authored matches
            # are found regardless of how many wiki pages also match (a bm25 over-fetch
            # over a 250-page corpus would otherwise bury the few short authored pages).
            try:
                return [r[0] for r in conn.execute(
                    "SELECT rowid FROM lore WHERE lore MATCH ? AND tier = ? ORDER BY rank LIMIT ?",
                    (match, tier, n),
                ).fetchall()]
            except sqlite3.OperationalError:
                return []

        cap = max(limit, 1)
        # Over-fetch each tier so the de-confliction can demote/drop contradicting hits
        # and still fill `cap` with clean ones (without it, dropping a top hit would just
        # shrink the result instead of promoting the next clean page).
        fetch = cap * 3 if supersedes else cap
        ids = _match_tier(0, fetch) + _match_tier(1, fetch)  # authored canon first, then wiki to fill
    finally:
        conn.close()
    tokens = re.findall(r"[A-Za-z0-9]+", query or "")
    subs = [s.lower() for s in (supersedes or []) if str(s).strip()]

    def _contradicts(excerpt: str) -> bool:
        low = excerpt.lower()
        return any(sub in low for sub in subs)

    seen: set[int] = set()
    clean: list[dict] = []
    demoted: list[dict] = []
    for rid in ids:
        if rid in seen:
            continue
        seen.add(rid)
        p = pages[rid]
        hit = {"title": p["title"], "excerpt": _excerpt(p["text"], tokens), "source": p["source"], "era": p.get("era", "")}
        # An excerpt that asserts a now-superseded fact is set aside (the page asserts a
        # fact the chosen ending retracted — the same predicate the overlay applied to
        # c.lore). It is DROPPED whenever any non-contradicting hit exists to take its
        # place; it is kept (DEMOTED, ranked last) ONLY if there is no clean alternative at
        # all, so the DM still gets *some* canon rather than an empty result. Either way it
        # never leads, and the header (if present) leads the whole set.
        (demoted if subs and _contradicts(hit["excerpt"]) else clean).append(hit)

    # Drop contradicting hits when shadowed by any clean hit; fall back to them only when
    # the entire match set is superseded (else the DM would get nothing on that query).
    out = clean[:cap] if clean else demoted[:cap]
    if canon_header:
        # Prepend the authoritative world-state as a synthetic leading hit. Counts toward
        # nothing the caller filters on; it just frames every page below it as background.
        out = [{"title": "CURRENT WORLD (authoritative)", "excerpt": canon_header, "source": "world-state", "era": ""}] + out
    return out


def page_count(world_id: str) -> int:
    return len(_pages(world_id))
