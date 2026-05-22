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

import os
import time
from pathlib import Path
from typing import Optional

from models import Campaign, SessionLogEntry


def state_dir() -> Path:
    raw = os.environ.get("CLAWDND_STATE_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".clawdnd" / "state"


def _campaign_dir(campaign_id: str) -> Path:
    return state_dir() / "campaigns" / campaign_id


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
