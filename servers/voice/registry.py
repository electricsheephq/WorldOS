"""Logical voice_id -> backend-native voice mapping.

Loaded from content/voices/voice-map.json. Each character/NPC carries a logical
voice_id (e.g. "narrator-dm", "companion-default"); the registry resolves it to
the active backend's real voice. Switching backends only re-points this map —
character data never changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from _env import env_var

_MAP_PATH = Path(
    env_var("VOICE_MAP")
    or Path(__file__).resolve().parents[2] / "content" / "voices" / "voice-map.json"
)


def _load() -> dict:
    if _MAP_PATH.exists():
        return json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    return {}


def resolve(voice_id: str, backend: str, default: Optional[str] = None) -> str:
    """Resolve a logical voice_id to the active backend's native voice.

    Falls back to an explicit default, then to the backend's first mapped voice,
    then (last resort) passes the voice_id through unchanged.
    """
    mapping = _load().get("backends", {}).get(backend, {})
    if voice_id in mapping:
        return mapping[voice_id]
    if default:
        return default
    if mapping:
        return next(iter(mapping.values()))
    return voice_id
