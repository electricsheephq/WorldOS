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
    # Version-stamp the snapshot: record the engine SHA that wrote it (cached, best-effort —
    # never aborts a save) and make sure schema_version is populated. schema_version's authority
    # is the manual constant on the Campaign model (default 1, bumped only on a breaking schema
    # change); we don't overwrite it here so an intentionally-pinned value survives a re-save.
    campaign.engine_sha = engine_sha()
    if not campaign.schema_version:
        campaign.schema_version = Campaign.model_fields["schema_version"].default
    path = _campaign_dir(campaign.id) / "snapshot.json"

    # F08-2 dirty-skip chokepoint: a load -> (no mutation) -> save_campaign is a PURE READ and
    # must not rewrite the snapshot or bump updated_at — otherwise it flips the #640
    # "most-recently-updated == live" pointer (a cross-campaign check_*/world_tick/scene_context
    # evaluation would silently steal the live pointer onto the inspected campaign). We detect a
    # no-op by serializing with the CURRENT (pre-stamp) updated_at and byte-comparing to disk: if
    # a tool mutated nothing, the candidate matches what we last wrote and we skip the write+stamp.
    # A genuine mutation -> bytes differ -> we stamp updated_at and write as usual (so a real write
    # is NEVER dropped). Note (F08-3 composition): after a TOLERANT load the in-memory dump differs
    # from the on-disk bytes (dropped keys are gone), so the candidate != disk and we DO write —
    # which is exactly why F08-3 stashes the original bytes independently BEFORE the drop.
    candidate = campaign.model_dump_json(indent=2)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == candidate:
            return path  # byte-identical -> nothing changed -> no rewrite, no fresh stamp
    except OSError:
        # Unreadable on-disk snapshot: fall through to a normal write (never skip a real save).
        pass

    campaign.updated_at = time.time()
    _atomic_write(path, campaign.model_dump_json(indent=2))
    return path


# F08-3: the most-recent set of unknown top-level keys the tolerant load dropped, per campaign id.
# Observable so the resume path can SURFACE schema-evolution data-loss instead of it being log-only
# (the audit's "make the drop observable" requirement). A clean strict load clears the entry.
_LAST_DROPPED_KEYS: dict[str, list[str]] = {}

# Sidecar name for the write-once pre-tolerant backup (F08-3). Lives beside snapshot.json; nothing
# globs ``campaigns/<id>/*.json`` (list_slots globs slots/*.json; listings read snapshot.json by
# name), so the sibling file is inert to the rest of the store.
_PRE_TOLERANT_BACKUP = "snapshot.pre-tolerant.json"


def last_dropped_keys(campaign_id: str) -> list[str]:
    """The unknown top-level keys the LAST tolerant load of this campaign dropped (F08-3).

    Empty when the most recent load was a clean strict parse (nothing dropped). The resume path
    reads this to surface schema-evolution data-loss to the operator/DM rather than leaving it
    log-only and invisible at the table — the original bytes remain recoverable from the
    ``snapshot.pre-tolerant.json`` sidecar this load stashes."""
    return list(_LAST_DROPPED_KEYS.get(campaign_id, []))


def _validate_tolerant(raw: str) -> tuple[Campaign, list[str]]:
    """Side-effect-free tolerant parse: drop unknown top-level keys, validate, and report which
    keys were dropped (F08-3/F08-4 shared core).

    PURE — no disk writes, no module-state mutation — so the enumerators (F08-4) can reuse it
    without turning a listing into a writer (the "pure reads don't write" invariant). Only the
    callable load path (:func:`_tolerant_load`) layers on the write-once backup + dropped-key
    recording. Raises RuntimeError if the snapshot is incompatible even after stripping unknown
    top-level keys (genuine corruption — not a schema-skew we can recover)."""
    import json
    data: dict = json.loads(raw)
    known = set(Campaign.model_fields)
    dropped = [k for k in list(data) if k not in known]
    for k in dropped:
        del data[k]
    try:
        return Campaign.model_validate(data), dropped
    except ValidationError as exc:
        raise RuntimeError(
            f"snapshot is incompatible with the current schema and cannot be loaded even "
            f"after stripping unknown top-level keys.  Validation error: {exc}"
        ) from exc


def _tolerant_load(campaign_id: str, raw: str) -> Campaign:
    """Tolerant load for the CALLABLE path: parse + record dropped keys + stash the original bytes.

    Wraps the pure :func:`_validate_tolerant` with the two F08-3 side effects (skipped by the
    read-only enumerators): recording the dropped key names so the resume path can SURFACE the
    loss, and stashing the ORIGINAL bytes write-once into ``snapshot.pre-tolerant.json`` so the
    unknown data is recoverable even after the next save rewrites snapshot.json in the current
    schema."""
    try:
        c, dropped = _validate_tolerant(raw)
    except RuntimeError as exc:
        # Preserve the historical load_campaign error wording (callers/tests match on it).
        raise RuntimeError(f"load_campaign({campaign_id!r}): {exc}") from exc
    _LAST_DROPPED_KEYS[campaign_id] = list(dropped)
    if dropped:
        log.warning(
            "load_campaign(%s): dropping unrecognised top-level key(s) %s "
            "— snapshot may be from a different schema version; the original bytes are "
            "stashed in %s and the data in those fields is lost for this load.",
            campaign_id,
            dropped,
            _PRE_TOLERANT_BACKUP,
        )
        # Stash the ORIGINAL bytes write-once BEFORE the drop takes effect on disk, so the unknown
        # fields stay recoverable even after the next save rewrites snapshot.json in the current
        # schema. Fixed filename (NOT timestamped) + skip-if-exists: the FIRST-skew bytes are the
        # valuable ones, and a fresh-timestamp name would mint an unbounded backup per tolerant
        # load. Best-effort: a backup-write failure must DEGRADE (log) — never abort the load (a
        # read must not brick because a sibling write failed).
        backup = _campaign_dir(campaign_id) / _PRE_TOLERANT_BACKUP
        if not backup.exists():
            try:
                _atomic_write(backup, raw)
            except OSError as exc:
                log.warning(
                    "load_campaign(%s): could not stash pre-tolerant backup %s: %s "
                    "(continuing — the load is unaffected).",
                    campaign_id, _PRE_TOLERANT_BACKUP, exc,
                )
    return c


def _load_summary(snap: Path) -> Optional[Campaign]:
    """Read-only tolerant parse of a snapshot file for the ENUMERATORS (F08-4).

    The enumerators (list_campaigns / campaigns_for_world / active_campaign_id) must use the SAME
    notion of "loadable" as load_campaign — otherwise a tolerant-loadable campaign is INVISIBLE to
    listings and to the #640 live-pointer resolver (the resolver would silently pick the OTHER,
    strict-only campaign). Strict parse first; tolerant retry only on strict failure. PURE: no
    backup write, no module-state mutation (those side effects belong to the callable load path
    only — a listing must never write). Genuinely-corrupt snapshots (RuntimeError) are skipped, so
    real corruption can't crash or pollute a listing."""
    try:
        raw = snap.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return Campaign.model_validate_json(raw)
    except ValidationError:
        pass
    try:
        c, _ = _validate_tolerant(raw)
        return c
    except (RuntimeError, ValueError):
        return None


def load_campaign(campaign_id: str) -> Optional[Campaign]:
    path = _campaign_dir(campaign_id) / "snapshot.json"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    try:
        c = Campaign.model_validate_json(raw)
        _LAST_DROPPED_KEYS.pop(campaign_id, None)  # clean strict load: nothing dropped
        return c
    except ValidationError:
        pass

    # Tolerant fallback: drop any top-level keys the current schema doesn't know about
    # (removed/renamed fields from an older or newer snapshot).  We only attempt this at the TOP
    # level of Campaign — sub-model strictness is intentionally preserved.  Per-rename migrations
    # (old-key → new-key) are added in _tolerant_load per-release as needed; this generic net only
    # handles field removal / unknown keys.
    return _tolerant_load(campaign_id, raw)


def _slots_dir(campaign_id: str) -> Path:
    return _campaign_dir(campaign_id) / "slots"


def _slot_path(campaign_id: str, slot: str) -> Path:
    """Path to a named save slot under a campaign, with the slot name validated as a flat
    segment (so 'quicksave' is fine but '../foo' / 'a/b' is rejected before any I/O)."""
    return _slots_dir(campaign_id) / f"{safe_path_segment(slot, 'slot')}.json"


def _slot_sessions_manifest_path(campaign_id: str, slot: str) -> Path:
    """Path to a slot's SESSION-LOG manifest sidecar (F08-1).

    Lives under ``slots/.manifests/<slot>.json`` — a dedicated subdir so the non-recursive
    ``slots/*.json`` glob in :func:`list_slots` never mistakes it for a restore point. The
    manifest records each session file's byte length at slot time, so :func:`load_slot` can
    roll the session logs back to match the rolled-back snapshot (a slot that restored only
    snapshot.json left a discarded timeline — an undone TPK, a post-slot orphan session —
    permanently canon in read_log_all / recap, the DM's lean-beat memory)."""
    return _slots_dir(campaign_id) / ".manifests" / f"{safe_path_segment(slot, 'slot')}.json"


def _session_files(campaign_id: str) -> dict[str, Path]:
    """The campaign's live session-log files: ``{filename: path}`` for ``sessions/*.jsonl``
    (non-recursive — the same glob shape read_log_all sees, so archived files under a
    ``rolled-back-*`` subdir are invisible here too)."""
    sessions_dir = _campaign_dir(campaign_id) / "sessions"
    if not sessions_dir.is_dir():
        return {}
    return {p.name: p for p in sessions_dir.glob("*.jsonl") if p.is_file()}


def save_slot(campaign_id: str, slot: str = "quicksave") -> Path:
    """Copy a campaign's CURRENT live snapshot into a named save slot.

    A slot is a point-in-time copy of the whole campaign aggregate, written atomically beside
    the live snapshot (campaigns/<id>/slots/<slot>.json). The live snapshot.json is the unit of
    persistence the engine already maintains, so we copy IT verbatim (not a re-serialized model)
    — the slot is byte-for-byte the campaign as last saved. Raises ValueError if the campaign has
    no live snapshot yet. Caller holds campaign_lock (sole-writer).

    F08-1: ALSO captures a SESSION-LOG manifest (each ``sessions/*.jsonl`` file's byte length at
    slot time) into a sidecar, so a later load_slot can roll the append-only logs back to match
    the rolled-back snapshot instead of leaving a discarded timeline canon in recap/lean-memory."""
    live = _campaign_dir(campaign_id) / "snapshot.json"
    if not live.exists():
        raise ValueError(f"no live snapshot for campaign {campaign_id!r} to save")
    data = live.read_text(encoding="utf-8")
    dest = _slot_path(campaign_id, slot)
    _atomic_write(dest, data)
    # Capture the session-log state alongside the snapshot. The manifest maps each live session
    # file name -> its current byte length; load_slot uses it to archive post-slot orphans and
    # truncate grown sessions back to here. Best-effort: a manifest-write failure must not abort
    # the slot save (the slot still restores the snapshot; logs degrade to today's behavior).
    import json
    try:
        manifest = {name: p.stat().st_size for name, p in _session_files(campaign_id).items()}
        _atomic_write(_slot_sessions_manifest_path(campaign_id, slot), json.dumps(manifest, indent=2))
    except OSError as exc:
        log.warning("save_slot(%s,%s): could not write sessions manifest: %s", campaign_id, slot, exc)
    return dest


def _rollback_sessions_to_manifest(campaign_id: str, slot: str) -> None:
    """Roll the campaign's session logs back to the state captured in a slot's manifest (F08-1).

    Called by :func:`load_slot` AFTER the snapshot is restored, under the caller's campaign_lock
    (sole-writer). For every live ``sessions/*.jsonl`` file:
      * NOT in the manifest (created after the slot) -> ARCHIVE the whole file (a post-slot
        orphan timeline) under ``sessions/rolled-back-<ts>/``;
      * LONGER than its manifest byte length (grown after the slot) -> archive the discarded
        TAIL, then truncate the live file back to the manifested length;
      * SHORTER than or equal to the manifest length -> LEAVE AS-IS (degrade, never pad/raise:
        a shorter file can arise after an intervening restore of an older slot).
    Archiving (not deleting) keeps the discarded timeline recoverable; the archive subdir is
    invisible to read_log_all's non-recursive ``*.jsonl`` glob, so recap/lean-memory match the
    rolled-back snapshot. A MANIFEST-LESS slot (legacy, pre-F08-1) is a no-op -> today's
    behavior (snapshot restored, logs untouched)."""
    import json
    mpath = _slot_sessions_manifest_path(campaign_id, slot)
    if not mpath.exists():
        return  # legacy / manifest-less slot: degrade to today (leave session logs alone)
    try:
        manifest: dict = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # A corrupt manifest must not brick a restore — degrade to today (logs untouched).
        log.warning("load_slot(%s,%s): unreadable sessions manifest, leaving logs as-is: %s",
                    campaign_id, slot, exc)
        return

    live = _session_files(campaign_id)
    if not live:
        return
    archive_dir = _campaign_dir(campaign_id) / "sessions" / f"rolled-back-{int(time.time() * 1000)}"
    for name, path in live.items():
        try:
            if name not in manifest:
                # A whole session created after the slot — archive it out of the live set.
                archive_dir.mkdir(parents=True, exist_ok=True)
                os.replace(path, archive_dir / name)
                continue
            kept_len = int(manifest[name])
            cur_len = path.stat().st_size
            if cur_len <= kept_len:
                continue  # unchanged or shorter — leave as-is (never pad/raise)
            # Grown: archive the discarded tail, then truncate the live file back to slot length.
            with open(path, "rb") as f:
                head = f.read(kept_len)
                tail = f.read()
            archive_dir.mkdir(parents=True, exist_ok=True)
            (archive_dir / name).write_bytes(tail)
            # Atomic truncate via temp-then-replace so a crash never leaves a half-written log.
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "wb") as f:
                f.write(head)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            # One unreadable/locked session file must not abort the whole rollback.
            log.warning("load_slot(%s,%s): could not roll back session %s: %s",
                        campaign_id, slot, name, exc)


def load_slot(campaign_id: str, slot: str = "quicksave") -> Campaign:
    """Restore a named save slot back over the live campaign snapshot.

    Reads the slot, validates it parses as a Campaign for THIS campaign id (a slot belongs to the
    campaign it was saved from — we refuse to clobber the live state with a foreign/corrupt
    snapshot), then writes it to live via save_campaign (atomic + version-stamped). Raises
    FileNotFoundError if the slot is absent and ValueError if it is corrupt or mismatched.
    OVERWRITES the live snapshot — caller holds campaign_lock and has confirmed intent.

    F08-1: ALSO rolls the append-only SESSION LOGS back to the slot's manifest, so a discarded
    timeline (an undone TPK, a post-slot orphan session) can't stay canon in read_log_all /
    recap (the DM's lean-beat memory). A manifest-less (legacy) slot leaves logs untouched."""
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
    # Fence the session logs to the slot AFTER the snapshot lands (both under the caller's lock).
    _rollback_sessions_to_manifest(campaign_id, slot)
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
        c = _load_summary(snap)  # F08-4: tolerant-loadable campaigns stay visible to listings
        if c is None:
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
        c = _load_summary(snap)  # F08-4: same tolerant "loadable" definition as load_campaign
        if c is None:
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
        c = _load_summary(snap)  # F08-4: a tolerant-loadable campaign must not be skipped here —
        if c is None:            # otherwise the #640 resolver silently picks the OTHER campaign
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
    # F08-5 newline-heal: if a prior append was torn (e.g. an OOM-kill mid-write left an
    # unterminated final line with no trailing '\n'), a blind append would concatenate THIS entry
    # onto that torn line — one physical line carrying both a broken JSON fragment and a good entry,
    # so the poison grows and the good entry is unreadable too. Prefix a newline when the file's
    # last byte isn't already one, fencing the torn fragment onto its own line (the reader then
    # skips just that one line). Runs under the caller's campaign_lock, so no writer race. A fresh
    # / empty file needs no prefix.
    prefix = ""
    try:
        if path.exists() and path.stat().st_size > 0:
            with open(path, "rb") as rf:
                rf.seek(-1, os.SEEK_END)
                if rf.read(1) != b"\n":
                    prefix = "\n"
    except OSError:
        # Can't inspect the tail (race/permissions) — degrade to a plain append (today's behavior).
        prefix = ""
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + entry.model_dump_json() + "\n")


def read_log(campaign_id: str, session_id: str) -> list[SessionLogEntry]:
    path = _campaign_dir(campaign_id) / "sessions" / f"{session_id}.jsonl"
    if not path.exists():
        return []
    entries: list[SessionLogEntry] = []
    bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # F08-5 reader tolerance: one TORN line (truncated mid-write by an OOM-kill, or a stray
        # non-JSON line) must NOT brick the whole read. A single ValidationError used to raise out
        # of read_log -> read_log_all -> recap_from_store -> start_session's recap, hand-repair the
        # only fix. Skip the unparseable line and keep the rest; the recap/resume path stays alive.
        # Acceptable loss: one bad entry, vs a bricked resume (the audit's explicit trade-off).
        try:
            entries.append(SessionLogEntry.model_validate_json(line))
        except ValidationError:
            bad += 1
    if bad:
        log.warning(
            "read_log(%s/%s): skipped %d unparseable session-log line(s) "
            "(torn/truncated write) — continuing with the readable entries.",
            campaign_id, session_id, bad,
        )
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
