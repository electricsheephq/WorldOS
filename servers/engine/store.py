"""Single-writer, atomic persistence for ClawDnD campaigns.

The whole Campaign aggregate is the unit of persistence: it's written to
snapshot.json with an atomic temp-file + os.replace, so a crash or compaction
never leaves a half-written campaign. A per-session append-only JSONL log
captures the narrative beat-by-beat for recaps and post-compaction recovery.

State lives outside the repo by default (~/.clawdnd/state), overridable with the
CLAWDND_STATE_DIR env var, so it survives plugin reinstalls and is independent of
the server's working directory.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from pathlib import Path
from typing import Optional

from models import Campaign, SessionLogEntry


def state_dir() -> Path:
    raw = os.environ.get("CLAWDND_STATE_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".clawdnd" / "state"


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
    path = _campaign_dir(campaign.id) / "snapshot.json"
    _atomic_write(path, campaign.model_dump_json(indent=2))
    return path


def load_campaign(campaign_id: str) -> Optional[Campaign]:
    path = _campaign_dir(campaign_id) / "snapshot.json"
    if not path.exists():
        return None
    return Campaign.model_validate_json(path.read_text(encoding="utf-8"))


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
