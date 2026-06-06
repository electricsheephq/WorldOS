"""Single-writer, atomic persistence for WorldOS campaigns.

The whole Campaign aggregate is the unit of persistence: it's written to
snapshot.json with an atomic temp-file + os.replace, so a crash or compaction
never leaves a half-written campaign. A per-session append-only JSONL log
captures the narrative beat-by-beat for recaps and post-compaction recovery.

State lives outside the repo by default (~/.worldos/state, falling back to the
legacy ~/.clawdnd/state), overridable with the WORLDOS_STATE_DIR env var
(the legacy CLAWDND_STATE_DIR still works for v1.x), so it survives plugin
reinstalls and is independent of the server's working directory.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from _env import env_var
from models import Campaign, SessionLogEntry

log = logging.getLogger(__name__)

# Resolved-once cache for the engine's short git SHA. `None` = not yet resolved; a string
# (possibly "") = resolved. We stamp this onto every saved snapshot so a campaign records the
# engine version that last wrote it. Sentinel-based so a genuine "" (git unavailable) is cached
# and we don't re-shell on every save.
_ENGINE_SHA: Optional[str] = None


def engine_sha() -> str:
    """The engine's short git commit SHA, resolved once and cached.

    Best-effort and never fatal: a missing git, a non-repo checkout, or any subprocess error
    yields "" (and that "" is cached, so we never re-shell). Run with cwd pinned to this module's
    directory so the SHA reflects the ENGINE repo regardless of the server's working directory."""
    global _ENGINE_SHA
    if _ENGINE_SHA is None:
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parent,
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            _ENGINE_SHA = out.stdout.strip()
        except Exception:
            # git missing / not a repo / timeout / anything — degrade to "", never abort a save.
            _ENGINE_SHA = ""
    return _ENGINE_SHA


def state_dir() -> Path:
    # WORLDOS_STATE_DIR preferred; CLAWDND_STATE_DIR is the warn-only v1.x fallback.
    raw = env_var("STATE_DIR")
    if raw:
        return Path(raw).expanduser()
    # No override: prefer the new ~/.worldos home if it already exists, else fall
    # back to the legacy ~/.clawdnd (no bulk migration — see issue #295, W0-E/4.2).
    worldos_home = Path.home() / ".worldos" / "state"
    if worldos_home.parent.exists():
        return worldos_home
    return Path.home() / ".clawdnd" / "state"


def safe_path_segment(value: str, kind: str = "id") -> str:
    """Validate a filesystem-shaped identifier (campaign id, world/adventure dir name) that
    gets joined into the state or content root. IDs are FLAT segment names — never absolute,
    never containing a path separator or '..' — so a hostile/buggy value like '../../etc' or
    '/tmp/x' can't escape the root (and can't create a lock dir outside it before the read
    even fails). Raises ValueError on an escaping value. Reused by content.py for world ids."""
    v = (value or "").strip()
    if (not v or v in (".", "..") or "/" in v or "\\" in v or "\x00" in v or os.path.isabs(v)):
        raise ValueError(f"unsafe {kind} {value!r}: must be a flat name, not a path")
    return v


def _campaign_dir(campaign_id: str) -> Path:
    return state_dir() / "campaigns" / safe_path_segment(campaign_id, "campaign_id")


@contextlib.contextmanager
def campaign_lock(campaign_id: str):
    """Exclusive advisory lock around a campaign's load -> mutate -> save
    critical section, so concurrent tool calls (including a future Tier-2
    companion sub-session) can't lost-update each other. POSIX flock; released
    on exit."""
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic on POSIX


def save_campaign(campaign: Campaign) -> Path:
    campaign.updated_at = time.time()
    # Version-stamp the snapshot: record the engine SHA that wrote it (cached, best-effort —
    # never aborts a save) and make sure schema_version is populated. schema_version's authority
    # is the manual constant on the Campaign model (default 1, bumped only on a breaking schema
    # change); we don't overwrite it here so an intentionally-pinned value survives a re-save.
    campaign.engine_sha = engine_sha()
    if not campaign.schema_version:
        campaign.schema_version = Campaign.model_fields["schema_version"].default
    path = _campaign_dir(campaign.id) / "snapshot.json"
    _atomic_write(path, campaign.model_dump_json(indent=2))
    return path


def load_campaign(campaign_id: str) -> Optional[Campaign]:
    path = _campaign_dir(campaign_id) / "snapshot.json"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    try:
        return Campaign.model_validate_json(raw)
    except ValidationError:
        pass

    # Tolerant fallback: drop any top-level keys the current schema doesn't know
    # about (removed/renamed fields from an older or newer snapshot).  We only
    # attempt this at the TOP level of Campaign — sub-model strictness is
    # intentionally preserved.  Per-rename migrations (old-key → new-key) are
    # added here per-release as needed; this generic net only handles field
    # removal / unknown keys.
    import json
    data: dict = json.loads(raw)
    known = set(Campaign.model_fields)
    dropped = [k for k in list(data) if k not in known]
    if dropped:
        log.warning(
            "load_campaign(%s): dropping unrecognised top-level key(s) %s "
            "— snapshot may be from a different schema version; "
            "data in those fields is lost for this load.",
            campaign_id,
            dropped,
        )
        for k in dropped:
            del data[k]

    try:
        return Campaign.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(
            f"load_campaign({campaign_id!r}): snapshot is incompatible with the "
            f"current schema and cannot be loaded even after stripping unknown "
            f"top-level keys.  Validation error: {exc}"
        ) from exc


def _slots_dir(campaign_id: str) -> Path:
    return _campaign_dir(campaign_id) / "slots"


def _slot_path(campaign_id: str, slot: str) -> Path:
    """Path to a named save slot under a campaign, with the slot name validated as a flat
    segment (so 'quicksave' is fine but '../foo' / 'a/b' is rejected before any I/O)."""
    return _slots_dir(campaign_id) / f"{safe_path_segment(slot, 'slot')}.json"


def save_slot(campaign_id: str, slot: str = "quicksave") -> Path:
    """Copy a campaign's CURRENT live snapshot into a named save slot.

    A slot is a point-in-time copy of the whole campaign aggregate, written atomically beside
    the live snapshot (campaigns/<id>/slots/<slot>.json). The live snapshot.json is the unit of
    persistence the engine already maintains, so we copy IT verbatim (not a re-serialized model)
    — the slot is byte-for-byte the campaign as last saved. Raises ValueError if the campaign has
    no live snapshot yet. Caller holds campaign_lock (sole-writer)."""
    live = _campaign_dir(campaign_id) / "snapshot.json"
    if not live.exists():
        raise ValueError(f"no live snapshot for campaign {campaign_id!r} to save")
    data = live.read_text(encoding="utf-8")
    dest = _slot_path(campaign_id, slot)
    _atomic_write(dest, data)
    return dest


def load_slot(campaign_id: str, slot: str = "quicksave") -> Campaign:
    """Restore a named save slot back over the live campaign snapshot.

    Reads the slot, validates it parses as a Campaign for THIS campaign id (a slot belongs to the
    campaign it was saved from — we refuse to clobber the live state with a foreign/corrupt
    snapshot), then writes it to live via save_campaign (atomic + version-stamped). Raises
    FileNotFoundError if the slot is absent and ValueError if it is corrupt or mismatched.
    OVERWRITES the live snapshot — caller holds campaign_lock and has confirmed intent."""
    src = _slot_path(campaign_id, slot)
    if not src.exists():
        raise FileNotFoundError(f"no save slot {slot!r} for campaign {campaign_id!r}")
    raw = src.read_text(encoding="utf-8")
    try:
        c = Campaign.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(
            f"save slot {slot!r} for campaign {campaign_id!r} is corrupt and cannot be restored: {exc}"
        ) from exc
    if c.id != campaign_id:
        raise ValueError(
            f"save slot {slot!r} belongs to campaign {c.id!r}, not {campaign_id!r}; refusing to restore"
        )
    save_campaign(c)  # atomic replace of the live snapshot.json + fresh version stamp
    return c


def list_slots(campaign_id: str) -> list[dict]:
    """Named save slots for a campaign (slot name + last-modified time), newest first.
    Read-only; skips any unreadable file so a half-written slot can't break the listing."""
    d = _slots_dir(campaign_id)
    out: list[dict] = []
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        out.append({"slot": p.stem, "updated_at": mtime})
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


def list_campaigns() -> list[dict]:
    root = state_dir() / "campaigns"
    out: list[dict] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        snap = d / "snapshot.json"
        if not snap.exists():
            continue
        try:
            c = Campaign.model_validate_json(snap.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({"id": c.id, "title": c.title, "updated_at": c.updated_at})
    return out


def campaigns_for_world(world_id: str) -> list[dict]:
    """Saved campaigns started from a given world seed — for resume / orphan warnings
    (re-running start_world otherwise mints a fresh campaign and abandons the old one)."""
    out: list[dict] = []
    root = state_dir() / "campaigns"
    if not world_id or not root.exists():
        return out
    for d in sorted(root.iterdir()):
        snap = d / "snapshot.json"
        if not snap.exists():
            continue
        try:
            c = Campaign.model_validate_json(snap.read_text(encoding="utf-8"))
        except Exception:
            continue
        if c.world_id == world_id:
            out.append({"id": c.id, "title": c.title, "day": c.day, "updated_at": c.updated_at})
    return out


def active_campaign_id(world_id: str = "") -> Optional[str]:
    """The campaign a harness should re-ground a LEAN beat against — the LIVE save,
    resolved deterministically as the MOST-RECENTLY-UPDATED campaign (largest
    ``updated_at``), optionally scoped to ``world_id``.

    Why this exists (issue #640 — lean re-ground cross-chronicle contamination):
    the play/QA harnesses used to pick the lean re-ground ``campaign_id`` by the
    LARGEST snapshot on disk (``qa/lib_beat_driver.sh:clawdnd_snapshot_path`` ->
    ``ls -S | head -1``). When TWO campaigns coexist in one state dir — a cold-open
    ``start_world`` retry minting a parallel campaign, or a stale prior save — the
    largest snapshot can be the WRONG (parallel) campaign. The engine's
    ``scene_context`` is strictly campaign-pure (it only ever reads
    ``campaigns/<id>/…`` for the id it is GIVEN), so pointing a fast/transcript-free
    lean beat at the wrong id faithfully folds a DIFFERENT save's opening scene
    (wrong HP, wrong day, wrong scene art) into the re-ground — the A/B-proven
    contamination. "Largest" is a fiction-volume proxy; "live" is **who wrote last**.

    The engine is the SOLE source of truth for which campaign is live, so the
    resolver lives here (the writer) and the harness asks the engine rather than
    guessing from file sizes. Read-only; returns the campaign id, or ``None`` when no
    matching campaign exists. Ties (equal ``updated_at``) break on the id for
    determinism. ``world_id`` filters to one world seed (the harness always knows the
    world it launched), so a stale save from a DIFFERENT world can never be selected.
    """
    root = state_dir() / "campaigns"
    if not root.exists():
        return None
    best_id: Optional[str] = None
    best_updated: float = float("-inf")
    for d in sorted(root.iterdir()):
        snap = d / "snapshot.json"
        if not snap.exists():
            continue
        try:
            c = Campaign.model_validate_json(snap.read_text(encoding="utf-8"))
        except Exception:
            continue
        if world_id and c.world_id != world_id:
            continue
        updated = c.updated_at or 0.0
        # Strict > keeps the FIRST seen on a tie; sorted(iterdir()) makes that the
        # lexicographically-smallest id, so the choice is fully deterministic.
        if updated > best_updated:
            best_updated = updated
            best_id = c.id
    return best_id


def append_log(campaign_id: str, session_id: str, entry: SessionLogEntry) -> None:
    path = _campaign_dir(campaign_id) / "sessions" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")


def read_log(campaign_id: str, session_id: str) -> list[SessionLogEntry]:
    path = _campaign_dir(campaign_id) / "sessions" / f"{session_id}.jsonl"
    if not path.exists():
        return []
    entries: list[SessionLogEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(SessionLogEntry.model_validate_json(line))
    return entries


def read_log_all(
    campaign_id: str, session_ids: Optional[list[str]] = None
) -> list[SessionLogEntry]:
    """Read EVERY session log of a campaign, concatenated in chronological order.

    READ-ONLY (the sole-writer invariant): only ever opens and parses the
    ``campaigns/<id>/sessions/*.jsonl`` files; never writes.

    Under lean / fast-turn play each beat starts a FRESH session id, so the
    CURRENT session's log can be empty even though the story-so-far lives in
    earlier session files — a per-session ``read_log`` would miss it. This walks
    them all so a campaign-wide tail (e.g. scene_context's ``recent_narration``)
    sees the last beats regardless of which session wrote them.

    Ordering is canonical-chronological:
      * sessions in the order the campaign opened them (``session_ids``, which the
        model documents as "play sessions in order") come first, in that order;
      * any *.jsonl on disk NOT named in ``session_ids`` (defensive: an orphaned
        or externally-added file) is appended afterwards, ordered by file mtime;
      * within each session, entries keep their on-disk (append) order.
    A final stable sort by each entry's timestamp ``t`` smooths any cross-file
    interleave while preserving append order for equal/zero timestamps.
    """
    sessions_dir = _campaign_dir(campaign_id) / "sessions"
    if not sessions_dir.is_dir():
        return []

    on_disk = {p.stem: p for p in sessions_dir.glob("*.jsonl") if p.is_file()}

    ordered_ids: list[str] = []
    seen: set[str] = set()
    for sid in session_ids or []:
        if sid in on_disk and sid not in seen:
            ordered_ids.append(sid)
            seen.add(sid)
    # Defensive tail: files present on disk but not listed in session_ids.
    leftover = [sid for sid in on_disk if sid not in seen]
    leftover.sort(key=lambda sid: on_disk[sid].stat().st_mtime)
    ordered_ids.extend(leftover)

    entries: list[SessionLogEntry] = []
    for sid in ordered_ids:
        entries.extend(read_log(campaign_id, sid))
    # Stable sort: keeps within-file append order for equal timestamps and orders
    # across files by wall-clock when timestamps are present.
    entries.sort(key=lambda e: e.t)
    return entries
