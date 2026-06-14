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
code is covered by the root WorldOS license and reads it generically — it never hard-codes
any setting.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from _env import env_var
from store import safe_path_segment  # path-containment guard for world ids


def _content_dir() -> Path:
    raw = env_var("CONTENT_DIR")
    return Path(raw).expanduser() if raw else Path(__file__).resolve().parents[2] / "content"


def _lore_dir(world_id: str) -> Optional[Path]:
    """content/worlds/<id>/lore/, falling back to the gitignored _private/ seed."""
    world_id = safe_path_segment(world_id, "world_id")  # defense-in-depth (no current raw-input path)
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


# F10-1 — tier-0 noise floor. An authored (tier-0) page keeps absolute precedence over the wiki
# tier only if its FTS |rank| is at least this FRACTION of the best-matching page overall — i.e.
# it must match RELATIVE to the strongest hit this query produced. The floor is intentionally
# relative (not a fixed absolute epsilon): bm25 |rank| magnitudes are corpus-size-dependent (in a
# 2-page corpus every match ranks ≈ 2e-6; in the 356-page shipped corpus genuine matches rank
# ≈ 5.8), so an absolute cutoff would wrongly demote a genuine authored page in a tiny corpus.
# The fraction is small (0.01) so it ONLY catches near-zero noise: a pure-stopword match ranks
# ~6 ORDERS OF MAGNITUDE below a real match (the "the Counting House" repro: noise ≈ 2e-6 vs best
# ≈ 8.97 → ratio ~3e-7 ≪ 0.01, demoted), while a genuine-but-modest authored match (≈ 5.8, ratio
# ~0.65) is comfortably kept. best == 0 (every page matched every token) → floor 0 → no demotion
# → today's behavior.
_NOISE_FLOOR_FRACTION = 0.01


def _safe_match(query: str) -> str:
    """OR-of-quoted-tokens (relevance, not all-terms-required) — same fix as recall.

    F10-1: DROP sub-2-char tokens. A possessive query like ``"Wyrm's Crossing"`` tokenizes to
    ``["Wyrm", "s", "Crossing"]``; the bare 1-char ``"s"`` matches the apostrophe-s of EVERY
    page and drags unrelated pages above the dedicated one (the possessive-query failure class).
    Single letters carry no retrieval signal, so we drop ``len < 2`` tokens — UNLESS that would
    empty the match (a degenerate all-short query), in which case we keep them so the search
    still runs rather than silently returning nothing. A query with no word-chars stays empty,
    exactly as before."""
    toks = re.findall(r"[A-Za-z0-9]+", query or "")
    kept = [t for t in toks if len(t) >= 2]
    if not kept:
        kept = toks  # all tokens were short — don't empty the match; search them as-is
    return " OR ".join(f'"{t}"' for t in kept)


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


# Sentence terminators we split a flattened page on for sentence-level redaction. Kept
# deliberately simple + stdlib-only (no NLP): `. ! ? ;` followed by whitespace. A
# trailing fragment with no terminator is its own sentence. (Markdown list dashes and
# inline `**bold**` markers are left intact — substrings are matched against the page's
# real bytes, so a `supersedes` phrase must occur as authored, embedded `**` and all.)
# AUTHORING CONTRACT: a `supersedes` substring must fall WITHIN one such sentence — a
# phrase straddling a `. `/`; ` boundary won't match (the two halves land in different
# sentences). Endings derive their substrings from a grep of the real corpus, so this is
# satisfied by construction; it only constrains future hand-authored substrings.
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+")


_ELISION = "[…superseded…]"


def _redact_superseded(text: str, subs: list[str]) -> tuple[str, bool, bool]:
    """Drop every SENTENCE of a (flattened) page whose lowercased text contains any
    `supersedes` substring, replacing it with an elision marker so the omission is
    visible. Returns (redacted_flat_text, any_redacted, gutted) where ``gutted`` is True
    only when redaction occurred AND no clean (non-elision) sentence survived.

    This is the granularity fix: the de-confliction decision is made per SENTENCE, not
    per page — a multi-fact page (e.g. ``baldurs-gate.md``: city description + "Gortash
    is dead") keeps its valid sentences and loses ONLY the superseded one, instead of the
    whole page being dropped (over-suppression) or escaping because the contradiction sat
    outside a 600-char excerpt window (under-suppression). Redaction runs on the FULL page
    so the subsequent excerpt — centered anywhere — can never surface a superseded sentence.

    `subs` must already be lowercased + non-empty (the caller gates on that)."""
    flat = " ".join(text.split())
    out: list[str] = []
    redacted = False
    clean_survived = False
    for sent in _SENT_SPLIT.split(flat):
        if not sent:
            continue
        if any(sub in sent.lower() for sub in subs):
            redacted = True
            # Elide rather than silently delete, so the DM sees a gap (and never the
            # superseded claim). Collapse consecutive elisions into one.
            if not (out and out[-1] == _ELISION):
                out.append(_ELISION)
        else:
            clean_survived = True
            out.append(sent)
    return " ".join(out).strip(), redacted, (redacted and not clean_survived)


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
      predicate `_apply_ending_overlay` applies to `c.lore`). Applied at SENTENCE
      granularity: any sentence of a returned page whose text contains a superseded
      substring (e.g. "Gortash is dead" under the tyranny ending) is REDACTED from that
      page's excerpt (replaced by an elision), and the PAGE IS KEPT — DEMOTED below
      unredacted pages so clean canon leads, but never dropped. This (a) preserves a
      multi-fact curated page's valid content instead of discarding the whole page (which
      let an unrelated page backfill the top-5), and (b) guarantees a contradicting
      sentence can never appear in a returned excerpt — including one that sat outside the
      old excerpt window — because redaction runs on the full page before it is excerpted.
      The result is never empty when there were matches. This closes the two-surface bug
      structurally (the .md corpus is de-conflicted on the same basis as c.lore).
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

        def _match_tier(tier: int, n: int) -> list[tuple[int, float]]:
            # Filter on the UNINDEXED tier column alongside MATCH so authored matches
            # are found regardless of how many wiki pages also match (a bm25 over-fetch
            # over a 250-page corpus would otherwise bury the few short authored pages).
            # Returns (rowid, rank) so the caller can apply the tier-0 NOISE FLOOR (F10-1):
            # FTS `rank` is negative; a smaller |rank| ≈ a weaker match (≈0 == matched only a
            # stopword), and we use |rank| to decide whether a tier-0 hit genuinely matches.
            try:
                return [(r[0], r[1]) for r in conn.execute(
                    "SELECT rowid, rank FROM lore WHERE lore MATCH ? AND tier = ? ORDER BY rank LIMIT ?",
                    (match, tier, n),
                ).fetchall()]
            except sqlite3.OperationalError:
                return []

        cap = max(limit, 1)
        # Over-fetch each tier so the de-confliction (and the F10-1 noise-floor demotion below)
        # can drop/demote a hit and still fill `cap` with clean ones — without it, dropping a
        # top hit would just shrink the result instead of promoting the next clean page. (The
        # noise floor needs the over-fetch too: a demoted noise-rank tier-0 page must be able to
        # be replaced by a genuinely-matching tier-1 page that the old `fetch=cap` never read.)
        fetch = cap * 3
        t0 = _match_tier(0, fetch)  # authored canon
        t1 = _match_tier(1, fetch)  # ingested wiki
    finally:
        conn.close()

    # F10-1 — tier-0 NOISE FLOOR (tighten the authored-canon precedence to GENUINE matches).
    # The tier-0-first guarantee exists so a short authored page isn't bm25-buried by the
    # 351-page wiki tier (the post-canon de-confliction guard). But a stopword-heavy query
    # ("the Counting House") makes a few authored pages match at NOISE rank (≈0, on "the"
    # alone), and the old absolute precedence let those noise matches fill the cap and bury the
    # dedicated wiki page. We keep tier-0-FIRST only for authored pages whose |rank| clears a
    # noise floor — within a bounded fraction of the best-matching page overall, or a tiny
    # absolute epsilon — and DEMOTE the rest below the wiki tier (kept as a fallback, never
    # dropped). A clean query (every tier-0 match is genuine) leaves `weak0` empty, so the order
    # reduces to today's `t0 + t1` and the output is byte-identical.
    best_overall = max((abs(r) for _, r in (t0 + t1)), default=0.0)
    floor = _NOISE_FLOOR_FRACTION * best_overall
    strong0 = [rid for rid, rank in t0 if abs(rank) >= floor]
    weak0 = [rid for rid, rank in t0 if abs(rank) < floor]
    ids = strong0 + [rid for rid, _ in t1] + weak0  # genuine authored, then wiki, then noise authored
    tokens = re.findall(r"[A-Za-z0-9]+", query or "")
    subs = [s.lower() for s in (supersedes or []) if str(s).strip()]

    seen: set[int] = set()
    kept: list[dict] = []
    gutted: list[dict] = []
    for rid in ids:
        if rid in seen:
            continue
        seen.add(rid)
        p = pages[rid]
        # SENTENCE-LEVEL redaction (not whole-page drop): when an ending supersedes a fact,
        # the page is KEPT — only the offending SENTENCE(s) are elided from its text — so a
        # multi-fact curated page (e.g. baldurs-gate.md = city + "Gortash is dead") keeps its
        # valid canon instead of being dropped (over-suppression → a shoe-shop page backfills
        # the top-5) or escaping because the contradiction sat outside the excerpt window
        # (under-suppression → the claim leaks). Redacting the FULL page before excerpting
        # guarantees a superseded sentence can never surface in the returned snippet, wherever
        # the excerpt centers. (subs empty → no redaction at all, so the excerpt is taken from
        # the raw page text exactly as before: byte-identical.)
        if subs:
            body, _redacted, is_gutted = _redact_superseded(p["text"], subs)
        else:
            body, is_gutted = p["text"], False
        hit = {"title": p["title"], "excerpt": _excerpt(body, tokens), "source": p["source"], "era": p.get("era", "")}
        # Demote ONLY a page that redaction GUTTED (every sentence was superseded — its
        # surviving excerpt is just an elision): a page with no real content left must not
        # lead, but it is still kept as a fallback (never empty). A page that merely had ONE
        # incidental sentence elided keeps its earned relevance rank — demoting it below a
        # barely-relevant clean page would re-introduce the very over-suppression we're fixing
        # (the curated faction page sinking under a shoe-shop page). FTS `rank` already orders
        # within each list, and authored (tier-0) ids precede wiki (tier-1) ids in `ids`.
        (gutted if is_gutted else kept).append(hit)

    # Pages with surviving content lead in their FTS/tier order; fully-gutted pages follow as
    # a fallback (so the result is never empty when there were matches). No valid page is
    # dropped, and a partially-redacted curated page keeps its rank above weaker clean hits.
    out = (kept + gutted)[:cap]
    if canon_header:
        # Prepend the authoritative world-state as a synthetic leading hit. Counts toward
        # nothing the caller filters on; it just frames every page below it as background.
        out = [{"title": "CURRENT WORLD (authoritative)", "excerpt": canon_header, "source": "world-state", "era": ""}] + out
    return out


def page_count(world_id: str) -> int:
    return len(_pages(world_id))
