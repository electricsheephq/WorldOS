"""Swappable image-generation layer for WorldOS.

Mirrors the voice TTS/STT design (see ../voice/interface.py, ../voice/stt.py): the
caller asks for one thing, generate(kind, prompt), and a provider selected by
WORLDOS_IMAGE_PROVIDER turns that request into an image descriptor. Providers are
interchangeable — a hosted model (OpenAI / Stability, later) or a null provider
that returns a deterministic placeholder for tests/CI — so swapping one out never
touches campaign state.

`kind` ∈ {"map", "portrait", "scene"}: a region/dungeon map, an NPC/PC portrait,
or a scene illustration. The DM picks the kind; the prompt is the visual brief.

Two layers, deliberately separate:

  - PROVIDER selection (get_provider) defaults to "null" and DEGRADES to null when
    a real provider is named but unconfigured (no API key wired) — so a
    misconfiguration never crashes the server, exactly like the STT selector.
  - A real provider, once selected and actually invoked, either produces an image
    or fails LOUDLY. The OpenAI/Stability backends are still stubs: invoking one
    raises NotImplementedError with a clear "set WORLDOS_IMAGE_* to wire" message.
    The "openclaw" backend is wired (it rides the local OpenClaw gateway's
    image_generate tool + its Codex OAuth — no raw API key here); when it can't
    reach/complete a generation it raises a clean RuntimeError so the caller can
    fall back to null. Either way, a selected real provider never silently no-ops.

Caching: when a provider returns bytes or a url, generate() writes a small JSON
descriptor under store.state_dir()/images/<scope>/<hash>.json, keyed by a content
hash of (kind, prompt, seed, provider). This cache is a STRICTLY-DERIVED,
rebuildable artifact — like the ledger's FTS index, it is NOT campaign state and
honors the engine's sole-writer invariant: it lives outside campaigns/, has no
independent source of truth, and can be deleted and regenerated at any time. The
null provider needs no network and no file to produce its placeholder.

stdlib only — no new dependency, and no network in the default/null path.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

import store
from _env import env_var, env_var_legacy

# The image kinds the engine knows how to ask for.
KINDS = ("map", "portrait", "scene")

# Selected when a real provider is named but not actually configured (no API key).
# get_provider() degrades to null in that case; surfaced here so tests can assert.
NOT_CONFIGURED = "[image provider not configured]"


@runtime_checkable
class ImageProvider(Protocol):
    """An image-generation backend.

    `generate` returns a JSON-serializable descriptor of the produced image. It
    must always include at least {"provider", "kind", "prompt"}. A provider that
    actually produced pixels returns either "bytes" (raw image bytes) or "url"; the
    null provider returns "placeholder": True and produces neither.
    """

    name: str

    def generate(self, kind: str, prompt: str, *, seed: Optional[int] = None) -> dict:
        ...


def _normalize_kind(kind: str) -> str:
    """Coerce to a known kind; unknown values map to 'scene' (the general case),
    so a typo never crashes generation — it just gets the generic illustration."""
    k = (kind or "").strip().lower()
    return k if k in KINDS else "scene"


# --------------------------------------------------------------------------- #
# Portrait prompt builder (pure — the primary unit-test target for #265).
# --------------------------------------------------------------------------- #

# Race/class shorthand the Create wizard emits (screen-create.jsx) → readable words.
# The wizard ids are SRD-ish shorthand ("half" = half-elf); a generated portrait brief
# should read naturally, so expand them here. Anything unmapped is humanized verbatim.
_RACE_WORDS = {
    "half": "half-elf",
    "half-elf": "half-elf",
    "half-orc": "half-orc",
    "halfelf": "half-elf",
    "halforc": "half-orc",
    "tiefling": "tiefling",
    "dragonborn": "dragonborn",
    "gnome": "gnome",
    "human": "human",
    "elf": "elf",
    "dwarf": "dwarf",
    "halfling": "halfling",
}
_CLASS_WORDS = {
    "fighter": "fighter",
    "wizard": "wizard",
    "rogue": "rogue",
    "cleric": "cleric",
    "bard": "bard",
    "paladin": "paladin",
    "ranger": "ranger",
    "barbarian": "barbarian",
    "sorcerer": "sorcerer",
    "warlock": "warlock",
    "monk": "monk",
    "druid": "druid",
}


def _humanize(token: str) -> str:
    """Turn a slug-ish id into readable words: 'half-orc'→'half-orc', 'true_neutral'→
    'true neutral'. Collapses separators to spaces, strips, lowercases. No PII source."""
    return " ".join(str(token or "").replace("_", " ").replace("-", " ").split()).strip().lower()


def _sanitize_cues(text: Optional[str], limit: int = 160) -> str:
    """Reduce free-text appearance cues to a single safe clause: strip newlines/control
    chars (so the prompt stays one line, never injecting structure), collapse whitespace,
    drop quotes/braces that could confuse a downstream prompt, and length-cap. Returns ''
    when there's nothing usable. This is the ONLY free-text that reaches the brief, so it
    is deliberately conservative — no names/PII are pulled from here by construction."""
    if not text:
        return ""
    cleaned = []
    for ch in str(text):
        if ch in "\r\n\t":
            cleaned.append(" ")
        elif ord(ch) < 32:
            continue  # drop other control chars
        elif ch in '"{}\\':
            continue  # strip prompt-structure punctuation
        else:
            cleaned.append(ch)
    out = " ".join("".join(cleaned).split()).strip()
    return out[:limit].rstrip(" ,;.")


def portrait_prompt(
    race: str,
    class_: str,
    *,
    name: Optional[str] = None,
    appearance: Optional[str] = None,
    alignment: Optional[str] = None,
) -> str:
    """Compose a tasteful, deterministic painterly portrait brief for a PC (#265).

    Pure function: the same inputs always yield the same single-line string, so it
    drives the cache hash AND is trivially unit-testable. It maps the wizard's race/
    class shorthand to readable words (``half`` → ``half-elf``; class id → readable
    name), folds in optional appearance cues + alignment as descriptive colour, and
    composes a Baldur's-Gate / Forgotten-Realms oil-painting brief.

    Deliberately carries NO PII and NO secrets: ``name`` is accepted (so callers can
    pass the wizard's full struct) but is intentionally NOT embedded in the brief — a
    portrait shouldn't bake a player's chosen name into the image, and it keeps the
    brief free of free-text identity. ``appearance`` is sanitized to one safe clause.
    Never emits a newline (a multi-line brief could smuggle prompt structure)."""
    race_w = _RACE_WORDS.get(_humanize(race).replace(" ", "-")) or _humanize(race) or "human"
    class_w = _CLASS_WORDS.get(_humanize(class_).replace(" ", "-")) or _humanize(class_) or "adventurer"
    cues = _sanitize_cues(appearance)
    align_w = _humanize(alignment)

    subject = f"a {race_w} {class_w}".strip()
    parts = [f"Character portrait of {subject}"]
    if cues:
        parts.append(cues)
    if align_w and align_w not in ("neutral", "true neutral"):
        parts.append(f"{align_w} bearing")
    brief = ", ".join(parts)
    brief += (
        ", head and shoulders, painterly fantasy oil-painting style, dramatic "
        "chiaroscuro lighting, Baldur's Gate / Forgotten Realms aesthetic, neutral "
        "dark background, single figure, no text, no watermark, no border"
    )
    # Guarantee single-line (the cue sanitizer already strips newlines, but be defensive).
    return " ".join(brief.split())


class NullImageProvider:
    """Placeholder image provider — used in CI, headless runs, and tests.

    Implements the ImageProvider protocol but generates no pixels and touches no
    network and no file, so the full image code path (selection, descriptor shape,
    tool wiring) is exercised without any model or API key. The descriptor is
    deterministic: the same (kind, prompt, seed) always yields the same dict, so
    tests and recaps can rely on it.
    """

    name = "null"

    def generate(self, kind: str, prompt: str, *, seed: Optional[int] = None) -> dict:
        return {
            "provider": self.name,
            "kind": _normalize_kind(kind),
            "prompt": prompt,
            "placeholder": True,
            "seed": seed,
        }


class _UnconfiguredHostedProvider:
    """Base for hosted, network-backed providers that aren't wired yet.

    These are the SEAM for later. Construction is cheap and never reads secrets, so
    selection can probe `configured()` freely; the heavy HTTP client would be
    imported lazily inside generate() once implemented (kept out of the base
    install). Until then, generate() raises NotImplementedError with a clear,
    actionable message rather than silently returning nothing.
    """

    name = "hosted"
    # The env var that would carry this provider's credential, e.g. an API key.
    api_key_env = "WORLDOS_IMAGE_API_KEY"

    def configured(self) -> bool:
        """True once the credential env var is set. get_provider() uses this to
        decide whether to hand back this provider or degrade to null."""
        return bool((env_var_legacy(self.api_key_env, "") or "").strip())

    def generate(self, kind: str, prompt: str, *, seed: Optional[int] = None) -> dict:
        raise NotImplementedError(
            f"{self.name} image provider not implemented yet — "
            f"set WORLDOS_IMAGE_PROVIDER=null for now, or wire this backend and "
            f"set {self.api_key_env} (plus WORLDOS_IMAGE_MODEL) to enable it."
        )


class OpenAIImageProvider(_UnconfiguredHostedProvider):
    """OpenAI image generation (stub seam).

    Real wiring would call the Images API (e.g. gpt-image-1 / DALL·E) over HTTPS
    using stdlib urllib or an optional client in an `image-openai` dependency
    group, read the model from WORLDOS_IMAGE_MODEL, and return {"bytes": ...} or
    {"url": ...} for generate() to cache. Needs WORLDOS_IMAGE_API_KEY.
    """

    name = "openai"
    api_key_env = "WORLDOS_IMAGE_API_KEY"


class StabilityImageProvider(_UnconfiguredHostedProvider):
    """Stability AI image generation (stub seam).

    Real wiring would call the Stability REST API (e.g. SDXL) over HTTPS, read the
    model/engine from WORLDOS_IMAGE_MODEL, and return image bytes for generate() to
    cache. Needs WORLDOS_IMAGE_API_KEY.
    """

    name = "stability"
    api_key_env = "WORLDOS_IMAGE_API_KEY"


class OpenClawImageProvider:
    """Generate images through the LOCAL OpenClaw gateway's `image_generate` tool.

    Unlike the OpenAI/Stability stubs, this one is REAL — but it carries no API key
    of its own. It rides the gateway's built-in image generation (model
    `openai/gpt-image-2`) and the gateway's existing ChatGPT/Codex OAuth profile,
    so the credential lives in the gateway, not here. See `openclaw_image.py` for
    the transport (stdlib HTTP against the always-on `/tools/invoke` endpoint) and
    the async/host-filesystem retrieval details.

    Selection: `WORLDOS_IMAGE_PROVIDER=openclaw`. The gateway URL/token/model are
    read from `WORLDOS_OPENCLAW_*` env (with `OPENCLAW_GATEWAY_TOKEN` /
    `OPENCLAW_GATEWAY_PASSWORD` accepted as token fallbacks).

    `configured()` is true once a gateway token is resolvable, so get_provider()
    hands this back; if no token is set it DEGRADES to null like the other hosted
    providers. Once invoked, ANY failure (gateway down/timeout/policy/no-image)
    raises cleanly — never a hang and never a silent no-op — so the caller can
    fall back to the null provider.

    The heavy client import is lazy (inside generate/configured), keeping
    `imagegen` import-time free of the client and any network.
    """

    name = "openclaw"

    def configured(self) -> bool:
        """True when a gateway bearer token is resolvable from env.

        Construction of the client is cheap and reads no secrets beyond env, so
        this is safe to probe during selection. Token from WORLDOS_OPENCLAW_GATEWAY_TOKEN
        or the OPENCLAW_GATEWAY_TOKEN / OPENCLAW_GATEWAY_PASSWORD fallbacks.
        """
        try:
            from openclaw_image import OpenClawImageClient
        except ImportError:
            return False
        return bool(OpenClawImageClient().token)

    def generate(self, kind: str, prompt: str, *, seed: Optional[int] = None) -> dict:
        """Generate via the gateway and return a cacheable image descriptor.

        On success the descriptor carries the produced image as `path` and/or
        `url` (and `bytes` when small), plus the gateway `task_id`. On any failure
        the underlying client raises OpenClawImageError / OpenClawGatewayUnreachable;
        we re-raise as RuntimeError so generate()'s caller can fall back to null.
        The null fallback is intentionally the caller's job — this provider, once
        invoked, fails loudly rather than silently degrading mid-generation.
        """
        from openclaw_image import OpenClawImageClient, OpenClawImageError

        try:
            result = OpenClawImageClient().generate_image(prompt)
        except OpenClawImageError as exc:
            # Includes OpenClawGatewayUnreachable. Re-raise as a clean, typed
            # failure so the caller can catch and fall back to null.
            raise RuntimeError(f"openclaw image provider failed: {exc}") from exc

        descriptor: dict = {
            "provider": self.name,
            "kind": _normalize_kind(kind),
            "prompt": prompt,
            "seed": seed,
            "placeholder": False,
        }
        if result.task_id:
            descriptor["task_id"] = result.task_id
        if result.path:
            descriptor["path"] = result.path
        if result.url:
            descriptor["url"] = result.url
        if result.mime_type:
            descriptor["mime_type"] = result.mime_type
        if result.data:
            # Keep the cache JSON-serializable: store bytes as base64 text.
            import base64

            descriptor["bytes_b64"] = base64.b64encode(result.data).decode("ascii")
        return descriptor


# Real, hosted providers keyed by WORLDOS_IMAGE_PROVIDER value.
_HOSTED: dict[str, type] = {
    "openai": OpenAIImageProvider,
    "stability": StabilityImageProvider,
    "openclaw": OpenClawImageProvider,
}


def provider_name() -> str:
    """The selected image provider name (env WORLDOS_IMAGE_PROVIDER, default 'null')."""
    return (env_var("IMAGE_PROVIDER", "null") or "null").strip().lower()


def get_provider() -> ImageProvider:
    """Construct the image provider selected by WORLDOS_IMAGE_PROVIDER.

    Defaults to the null provider. A named hosted provider (openai/stability) is
    returned only if it's actually configured (its API-key env var is set);
    otherwise — including unknown names — this DEGRADES to the null provider so a
    misconfiguration never crashes the server. (Invoking a hosted provider
    directly, bypassing this selector, still raises NotImplementedError — that's
    the intentional seam.)
    """
    name = provider_name()
    hosted_cls = _HOSTED.get(name)
    if hosted_cls is not None:
        provider = hosted_cls()
        if provider.configured():
            return provider
        # Named but unconfigured -> degrade to null (graceful, like the STT selector).
    return NullImageProvider()


# --------------------------------------------------------------------------- #
# Content-hash cache (strictly-derived, rebuildable — NOT campaign state).
# --------------------------------------------------------------------------- #

def _images_dir(scope: Optional[str] = None) -> Path:
    """Cache root for image descriptors, outside campaigns/ so it can never be
    mistaken for authoritative state. `scope` (e.g. a world or campaign id)
    partitions the cache; it's sanitized to a safe path segment."""
    root = store.state_dir() / "images"
    seg = _safe_scope(scope)
    return root / seg if seg else root


def _safe_scope(scope: Optional[str]) -> str:
    """Reduce an arbitrary scope id to a single safe path segment (alnum, -, _),
    so a caller-supplied world/campaign id can't escape the cache dir."""
    if not scope:
        return ""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(scope))[:128]


# --------------------------------------------------------------------------- #
# Ingested-art catalog consult (F11-6) — don't spend to regenerate art that the
# 2,359-dir _private ingest already provides. The VIEWER resolves ingested art FIRST
# (_latest_descriptor -> _ingested_descriptor across all _private worlds), so a
# generated canon-scope image is NEVER displayed when ingested art exists — pure spend.
# This is the engine-side gate: consult the catalog before enqueuing a worker.
#
# Read-only: the engine reading content/_private is allowed (it never writes there, and
# the sole-writer invariant covers campaign state, not the gitignored art tree). Mirrors
# the viewer's resolution conservatively — exact safe-scope dir, then a normalized
# scope-key match — so at worst a slug-drift miss falls through to generate (never a
# wrong skip of a genuinely-absent scope).
# --------------------------------------------------------------------------- #

# Leading kind/entity prefix tokens dropped when normalizing a scope to a NAME key —
# kept in sync with the viewer's _SCOPE_PREFIXES so the engine and viewer agree on what
# "the same scope" means (portrait-npc-shadowheart and portrait:shadowheart both ->
# shadowheart). A drift here only costs a redundant regen, never a wrong skip.
_SCOPE_PREFIXES = frozenset({
    "portrait", "scene", "item", "map", "npc", "char", "pc", "loc", "location",
    "region", "scope", "faction", "creature", "class", "race",
})


def _scope_key(scope: Optional[str]) -> str:
    """Normalize a scope to a separator/prefix-agnostic NAME key (mirrors the viewer's
    _scope_key): lowercase; unify `:`/`_`/space to `-`; drop leading kind/entity prefix
    tokens. So portrait-<id> / portrait:<slug> reconcile to the same key."""
    s = str(scope or "").lower().replace(":", "-").replace("_", "-").replace(" ", "-")
    toks = [t for t in s.split("-") if t]
    while toks and toks[0] in _SCOPE_PREFIXES:
        toks.pop(0)
    return "-".join(toks)


def _ingested_art_root() -> Optional[Path]:
    """Root of the gitignored _private ingested-art tree, world-neutral:
    <content>/worlds/_private/. Honors the same env contract the viewer uses so a
    cross-checkout launcher (the .app / a Lexar worktree running code from one checkout
    while the private art lives in the canonical one) resolves the right tree:
    WORLDOS_ART_REPO_ROOT/WORLDOS_ART_REPO_ROOT (an art checkout), then
    WORLDOS_REPO_ROOT/WORLDOS_REPO_ROOT, then CONTENT_DIR, then the in-repo content/.
    Returns None when no _private tree exists (art-less host -> caller falls through)."""
    candidates: list[Path] = []
    for raw in (env_var("ART_REPO_ROOT"), env_var("REPO_ROOT")):
        if raw:
            candidates.append(Path(raw).expanduser() / "content" / "worlds" / "_private")
    content_raw = env_var("CONTENT_DIR")
    if content_raw:
        candidates.append(Path(content_raw).expanduser() / "worlds" / "_private")
    # In-repo fallback: servers/engine/imagegen.py -> repo root is parents[2].
    candidates.append(Path(__file__).resolve().parents[2] / "content" / "worlds" / "_private")
    for c in candidates:
        try:
            if c.is_dir():
                return c
        except OSError:
            continue
    return None


def has_ingested_art(scope: Optional[str]) -> bool:
    """True when the _private ingest already provides a SERVABLE wiki_ingest.json for
    `scope` (F11-6). Mirrors the viewer's ingested-first resolution conservatively:
    an exact safe-scope dir match, else a normalized scope-key match across all
    _private worlds. Read-only; path-containment-guarded; never raises (a probe error
    is treated as "no art" so the caller still generates rather than crash)."""
    seg = _safe_scope(scope)
    if not seg:
        return False
    root = _ingested_art_root()
    if root is None:
        return False
    try:
        root_resolved = root.resolve()
    except OSError:
        return False
    # 1. Exact safe-scope dir (the common, fast case).
    try:
        for world_dir in root.iterdir():
            if not world_dir.is_dir():
                continue
            desc = world_dir / "images" / seg / "wiki_ingest.json"
            if not desc.exists():
                continue
            try:
                if root_resolved in desc.resolve().parents:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    # 2. Normalized scope-key fallback (UI engine-id scope vs manifest slug).
    want = _scope_key(scope)
    if not want:
        return False
    try:
        for world_dir in root.iterdir():
            images_dir = world_dir / "images"
            if not images_dir.is_dir():
                continue
            for sub in images_dir.iterdir():
                desc = sub / "wiki_ingest.json"
                if not desc.exists():
                    continue
                try:
                    if root_resolved not in desc.resolve().parents:
                        continue
                    d = json.loads(desc.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and _scope_key(d.get("scope")) == want:
                    return True
    except OSError:
        return False
    return False


def content_hash(kind: str, prompt: str, *, seed: Optional[int] = None, provider: str = "null") -> str:
    """Stable content hash for a generation request. Same inputs -> same key, so a
    repeat request hits the cache instead of regenerating. Independent of wall-clock
    time and dict ordering."""
    payload = json.dumps(
        {"kind": _normalize_kind(kind), "prompt": prompt, "seed": seed, "provider": provider},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_path(hash_: str, scope: Optional[str] = None) -> Path:
    return _images_dir(scope) / f"{hash_}.json"


def error_path(hash_: str, scope: Optional[str] = None) -> Path:
    """Sibling sidecar for a FAILED generation (F11-7): <scope>/<hash>.error.

    Deliberately a bare ``.error`` suffix (NOT ``.error.json``) so the viewer's
    descriptor resolvers — which glob ``*.json`` only — never pick it up as a real
    descriptor. It is a derived, rebuildable artifact like the rest of the cache."""
    return _images_dir(scope) / f"{hash_}.error"


def _atomic_write(path: Path, data: str) -> None:
    """Atomic temp-file + os.replace write, mirroring store._atomic_write, so a
    crash never leaves a half-written descriptor in the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def cache_read(hash_: str, scope: Optional[str] = None) -> Optional[dict]:
    """Return the cached descriptor for `hash_`, or None on a miss. A corrupt entry
    is treated as a miss (the cache is rebuildable, never load-bearing)."""
    path = cache_path(hash_, scope)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def cache_write(descriptor: dict, scope: Optional[str] = None) -> Path:
    """Write a descriptor into the derived cache, keyed by its own content hash.

    The hash is derived from the descriptor's (kind, prompt, seed, provider), so
    cache_write(generate(...)) round-trips with cache_read. Returns the path
    written. This is a derived artifact — safe to delete and regenerate.
    """
    hash_ = content_hash(
        descriptor.get("kind", ""),
        descriptor.get("prompt", ""),
        seed=descriptor.get("seed"),
        provider=descriptor.get("provider", "null"),
    )
    record = dict(descriptor)
    record.setdefault("hash", hash_)
    record.setdefault("cached_at", time.time())
    path = cache_path(hash_, scope)
    _atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))
    return path


def write_error(hash_: str, error: str, scope: Optional[str] = None, *,
                provider: str = "", kind: str = "", prompt: str = "") -> Path:
    """Record a FAILED background generation as a derived ``.error`` sidecar (F11-7).

    Today a background-worker generation failure is completely silent — generate()'s
    degrade-to-null result is discarded by the worker, leaving no artifact, no event,
    nothing under the images/ tree. That makes a failed provider lane indistinguishable
    from "not yet generated", which the image-evidence gate cannot classify. This writes
    a small JSON observability record next to where the descriptor WOULD have landed,
    keyed by the same content hash, via the same atomic writer. Sole-writer-safe: it
    touches only the derived, rebuildable image cache, never snapshot.json.

    Returns the path written."""
    record = {
        "hash": hash_,
        "status": "error",
        "error": str(error)[:500],
        "provider": provider,
        "kind": kind,
        "prompt": prompt,
        "failed_at": time.time(),
    }
    path = error_path(hash_, scope)
    _atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))
    return path


def read_error(hash_: str, scope: Optional[str] = None) -> Optional[dict]:
    """Return the ``.error`` sidecar for `hash_`, or None if none/corrupt (F11-7)."""
    path = error_path(hash_, scope)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def clear_error(hash_: str, scope: Optional[str] = None) -> None:
    """Delete any stale ``.error`` sidecar for `hash_` (F11-7).

    Called when a generation SUCCEEDS so a later success supersedes an earlier failure
    record — the sidecar reflects the most recent outcome, never a fossil. Missing file
    is a no-op; never raises (the cache is rebuildable)."""
    path = error_path(hash_, scope)
    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        pass


def _strip_inline_bytes(descriptor: dict) -> dict:
    """Return a shallow copy of `descriptor` with the inline image payload replaced by
    metadata (F11-5). A provider-lane descriptor may carry `bytes_b64` (base64 of the raw
    image, up to MAX_INLINE_BYTES=16MB); returning that verbatim from the generate_image
    tool injects megabytes of base64 into the DM's context for ONE beat. No tool-side
    consumer reads the bytes — only the viewer (/image, /portrait-*) does, and it reads the
    on-disk cache entry, not this return value. So we hand the tool a compact, equivalent
    descriptor: drop `bytes_b64`, add `has_bytes` + `byte_len` so a caller can still tell
    an image landed. Pure: the input dict and the on-disk cache file are untouched."""
    b64 = descriptor.get("bytes_b64")
    if not isinstance(b64, str) or not b64:
        return descriptor
    out = dict(descriptor)
    out.pop("bytes_b64", None)
    out["has_bytes"] = True
    # Approximate decoded byte length from the base64 text length (4 b64 chars -> 3 bytes),
    # net of '=' padding — cheap and exact enough for an at-a-glance size, no decode needed.
    pad = b64.count("=")
    out["byte_len"] = max(0, (len(b64) * 3) // 4 - pad)
    return out


def _newest_descriptor(scope: Optional[str]) -> Optional[dict]:
    """Most-recently-written descriptor under a scope dir, parsed. None on miss/corrupt.

    Mirrors the viewer's _newest_json_descriptor resolution (the cache is rebuildable,
    so a corrupt entry is just skipped, never fatal)."""
    cdir = _images_dir(scope)
    if not cdir.is_dir():
        return None
    newest: Optional[Path] = None
    newest_mtime = -1.0
    for p in cdir.glob("*.json"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest_mtime:
            newest, newest_mtime = p, m
    if newest is None:
        return None
    try:
        d = json.loads(newest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return d if isinstance(d, dict) else None


def copy_scope(src_scope: str, dst_scope: str) -> Optional[Path]:
    """Re-key the newest generated portrait from one scope to another (#265).

    The Create wizard generates a PC portrait to a PROVISIONAL content-scope
    (``portrait-pc-<hash>``) because the player's character has no engine id yet; the
    real opaque ``char_…`` id is only minted later at session start. Once it exists,
    every render surface keys that PC's face as ``portrait-<char_id>``. This helper
    bridges that gap: it copies the newest descriptor from ``src_scope`` into
    ``dst_scope`` (re-keyed via cache_write), so the unique face attaches to the real
    PC on every screen.

    The descriptor's image payload travels with it: ``bytes_b64`` / ``url`` are inline
    and copy verbatim; a ``path`` points at the gateway media file (outside both scope
    dirs) and stays valid — the viewer serves it through its containment allowlist
    regardless of which scope dir the descriptor lives in. The freshly-written copy
    gets a recomputed ``hash`` + ``cached_at`` so it's a self-consistent entry.

    Returns the written path, or None when the source has no (readable) descriptor —
    a miss is benign (the caller falls back to the gallery face). Never raises on a
    missing/corrupt source; like the rest of this module it treats the cache as
    rebuildable, never load-bearing. Writes ONLY the derived cache (honors the engine's
    sole-writer invariant — it never touches snapshot.json)."""
    src = _newest_descriptor(src_scope)
    if src is None:
        return None
    record = dict(src)
    # Drop the self-describing fields so cache_write recomputes them fresh for the
    # destination entry (otherwise the copy would carry the source's stale hash).
    record.pop("hash", None)
    record.pop("cached_at", None)
    return cache_write(record, scope=dst_scope)


def generate(
    kind: str,
    prompt: str,
    *,
    seed: Optional[int] = None,
    scope: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    """Generate (or recall from cache) an image descriptor for a prompt.

    Selects the active provider, returns a cached descriptor on a content-hash hit,
    otherwise asks the provider to generate. When the provider produces real output
    (bytes or url), the descriptor is written into the derived cache. The null
    provider's placeholder is cached too, so the same request is stable and free on
    repeat. `scope` (a world/campaign id) partitions the cache.
    """
    provider = get_provider()
    key = content_hash(kind, prompt, seed=seed, provider=provider.name)

    if use_cache:
        hit = cache_read(key, scope)
        if hit is not None:
            hit["cache_hit"] = True
            return hit

    try:
        descriptor = provider.generate(kind, prompt, seed=seed)
    except Exception as exc:
        # The skill promises generate_image is "always safe — a cheap no-op when no
        # provider/gateway is available." Honor it: a hosted/gateway provider that fails
        # (gateway down, timeout, auth, policy) must NOT crash the DM's turn. Degrade to
        # the null placeholder so play continues + the dashboard shows its placeholder.
        # Do NOT cache the degraded result — a transient gateway blip must be retryable.
        descriptor = NullImageProvider().generate(kind, prompt, seed=seed)
        descriptor["cache_hit"] = False
        descriptor["degraded_from"] = getattr(provider, "name", "provider")
        descriptor["error"] = str(exc)[:200]
        # F11-7: a failed generation used to vanish silently — the worker discarded this
        # degraded descriptor, leaving no artifact. Record a derived `.error` sidecar so the
        # failure is observable (and the image-evidence gate can classify it as `error`,
        # distinct from "not yet generated"). Keyed by the FAILED provider's content hash so
        # a later success under the same key clears it. Best-effort: a sidecar write must
        # never itself crash the degrade path. The degraded descriptor stays UNCACHED (a
        # transient gateway blip must remain retryable).
        if use_cache:
            try:
                write_error(
                    key, descriptor["error"], scope,
                    provider=getattr(provider, "name", "provider"),
                    kind=_normalize_kind(kind), prompt=prompt,
                )
            except Exception:  # pragma: no cover - sidecar is observability, never load-bearing
                pass
        return descriptor

    descriptor["cache_hit"] = False
    if use_cache:
        cache_write(descriptor, scope)
        # F11-7: a success supersedes any earlier failure record for this key, so the
        # sidecar always reflects the most recent outcome (never a fossil `.error`).
        clear_error(key, scope)

    return descriptor


# --------------------------------------------------------------------------- #
# Non-blocking generation — off the synchronous DM-turn path (latency).
#
# The DM calls generate_image as a *fire-and-forget* overlay: "the image is an
# optional overlay that never blocks the engine or the DM." With the null provider
# generate() is ~instant, but a real provider (openclaw) polls the gateway media dir
# up to DEFAULT_POLL_TIMEOUT (180s) — blocking the DM's turn for tens of seconds in
# real play. async_generate() returns IMMEDIATELY (<500ms) with the cache scope/hash
# the viewer already keys off, and a background worker does the actual generate()
# (provider call + atomic cache write). The viewer's /image?scope=… 404s → placeholder
# until the descriptor lands, so a not-yet-ready image degrades gracefully.
#
# Invariant safety: the worker calls the SAME generate() the synchronous path uses, so
# it writes ONLY the derived, rebuildable image cache (<state>/images/<scope>/…) via the
# existing atomic writer — never snapshot.json. The engine stays the SOLE writer of
# campaign state; this touches only the one derived artifact a background writer may.
# --------------------------------------------------------------------------- #

# Guards the in-flight set so two concurrent requests for the SAME (key, scope) don't
# both spawn a worker (the second would redo the provider's slow generation pointlessly).
_inflight_lock = threading.Lock()
_inflight: set[tuple[str, Optional[str]]] = set()


def _worker(kind: str, prompt: str, seed: Optional[int], scope: Optional[str],
            key: str) -> None:
    """Background thread body: run the real (possibly slow) generation and let
    generate() write the result into the derived cache. NEVER raises out of the
    thread — generate() already degrades a provider failure to the null placeholder,
    and we swallow anything else (the cache is rebuildable; a failed background job
    just means the viewer keeps its placeholder and a later call can retry)."""
    try:
        generate(kind, prompt, seed=seed, scope=scope)
    except Exception:
        # Defensive: generate() is already crash-proof, but a background thread must
        # never propagate. A miss is benign — the viewer shows its placeholder.
        pass
    finally:
        with _inflight_lock:
            _inflight.discard((key, scope))


# --------------------------------------------------------------------------- #
# F11-3: detached resolver — art that survives the per-beat `claude -p` exit.
#
# scripts/play.sh runs ONE `claude -p` per beat (--resume per beat); the engine MCP
# server is a stdio CHILD of each `claude -p`. When the interpreter exits at end of beat,
# its daemon threads (the _worker above) die abruptly mid-provider-call. A real provider
# (openclaw) polls the gateway media dir up to 180s, so art started late in a beat is
# LOST — and worse, the paid image is never claimed (a later identical call cache-misses,
# re-POSTs new spend, and its pre-POST snapshot already contains the orphaned PNG).
#
# Fix: spawn a process-group-DETACHED subprocess (start_new_session=True, stdio to
# DEVNULL) running THIS module's `--resolve` entrypoint. It outlives the parent and calls
# the SAME generate() — writing only the derived, rebuildable cache via the atomic writer
# (sole-writer-safe: never snapshot.json). A `generating` marker is written first so a
# re-POST within a TTL is suppressed (no double spend). Opt-in via env so today's default
# (daemon-thread) behavior is unchanged; the marker degrades like today's 404→placeholder.
# --------------------------------------------------------------------------- #

ENV_DETACHED_RESOLVER = "IMAGE_DETACHED_RESOLVER"
# How long a `generating` marker suppresses a re-spawn (seconds). Just over the gateway's
# own ~180s poll budget so a still-running resolver isn't double-spawned, but stale markers
# from a crashed resolver expire and let a later call retry.
ENV_GENERATING_TTL = "IMAGE_GENERATING_TTL"
DEFAULT_GENERATING_TTL = 210.0


def _detached_resolver_enabled() -> bool:
    """True when the detached-resolver path is opted in via env (default OFF, so the
    daemon-thread behavior is preserved exactly — additive, no behavior change today)."""
    return (env_var(ENV_DETACHED_RESOLVER, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _generating_ttl() -> float:
    try:
        v = env_var(ENV_GENERATING_TTL)
        return float(v) if v else DEFAULT_GENERATING_TTL
    except (TypeError, ValueError):
        return DEFAULT_GENERATING_TTL


def generating_path(hash_: str, scope: Optional[str] = None) -> Path:
    """Sibling `generating` marker for an in-flight detached resolution (F11-3).

    Bare `.generating` suffix (NOT `.json`) so the viewer's `*.json` descriptor glob
    never treats it as a real descriptor. Derived, rebuildable, sole-writer-safe."""
    return _images_dir(scope) / f"{hash_}.generating"


def _generating_is_fresh(hash_: str, scope: Optional[str]) -> bool:
    """True when a `generating` marker exists and is younger than the TTL — meaning a
    resolver is (still plausibly) in flight for this key, so a re-spawn would double-spend.
    A stale/missing/corrupt marker returns False so a later call can retry."""
    path = generating_path(hash_, scope)
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        started = float(rec.get("started_at", 0.0))
    except (OSError, ValueError, TypeError):
        return False
    return (time.time() - started) < _generating_ttl()


def _write_generating_marker(hash_: str, scope: Optional[str], *, pid: Optional[int] = None) -> None:
    """Write the `generating` marker that suppresses a re-spawn (F11-3). Best-effort."""
    rec = {"hash": hash_, "status": "generating", "started_at": time.time()}
    if pid is not None:
        rec["resolver_pid"] = pid
    try:
        _atomic_write(generating_path(hash_, scope), json.dumps(rec, ensure_ascii=False, indent=2))
    except OSError:  # pragma: no cover - marker is advisory, never load-bearing
        pass


def _clear_generating_marker(hash_: str, scope: Optional[str]) -> None:
    """Remove the `generating` marker (resolver finished/failed). Never raises."""
    try:
        generating_path(hash_, scope).unlink()
    except (FileNotFoundError, OSError):
        pass


def _spawn_detached_resolver(kind: str, prompt: str, seed: Optional[int],
                             scope: Optional[str], key: str, pname: str) -> bool:
    """Spawn a process-group-detached resolver subprocess for one generation (F11-3).

    Returns True if a resolver was spawned, False if a young `generating` marker meant we
    suppressed the spawn (a resolver is already in flight for this key). The subprocess
    runs THIS module's `--resolve` entrypoint under `sys.executable`, detached via
    start_new_session=True with stdio to DEVNULL, so it survives the parent `claude -p`
    exit. Falls back to the in-process daemon worker if the subprocess can't be launched
    (so art is never worse off than today)."""
    import subprocess
    import sys

    # Suppress a re-spawn while a young resolver is still plausibly running (no double spend).
    if _generating_is_fresh(key, scope):
        return False

    # Mark generating BEFORE the spawn so a racing call sees it immediately. The resolver
    # re-stamps with its own pid and clears the marker when done.
    _write_generating_marker(key, scope)

    payload = json.dumps({
        "kind": kind, "prompt": prompt, "seed": seed, "scope": scope,
        "key": key, "provider": pname,
        "state_dir": str(store.state_dir()),
    })
    try:
        subprocess.Popen(
            [sys.executable, _resolver_module_path(), "--resolve", payload],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach from the parent's process group/session
            close_fds=True,
            env=dict(os.environ),    # carry WORLDOS_*/WORLDOS_* provider + gateway config
        )
        return True
    except (OSError, ValueError):
        # Couldn't launch the detached process — fall back to the in-process daemon worker
        # so we degrade no worse than today. Clear our pre-spawn marker first.
        _clear_generating_marker(key, scope)
        t = threading.Thread(
            target=_worker, args=(kind, prompt, seed, scope, key),
            name=f"imagegen-fb-{key[:8]}", daemon=True,
        )
        with _inflight_lock:
            self_spawned = (key, scope) not in _inflight
            if self_spawned:
                _inflight.add((key, scope))
        if self_spawned:
            t.start()
        return self_spawned


def _resolver_module_path() -> str:
    """Absolute path to THIS module, so the detached subprocess runs the same resolver
    code regardless of the parent's cwd or sys.path."""
    return str(Path(__file__).resolve())


def _resolve_entry(spec: dict) -> int:
    """Detached-resolver body (run in the child subprocess). Does the real generate()
    and writes the derived cache, then clears the `generating` marker. NEVER raises out
    (a resolver failure is benign — generate() already wrote a `.error` sidecar and the
    viewer keeps its placeholder). Returns a process exit code."""
    kind = spec.get("kind", "")
    prompt = spec.get("prompt", "")
    seed = spec.get("seed")
    scope = spec.get("scope")
    key = spec.get("key", "")
    try:
        _write_generating_marker(key, scope, pid=os.getpid())
        generate(kind, prompt, seed=seed, scope=scope)
        return 0
    except Exception:
        return 0  # benign: degrade is already handled inside generate()
    finally:
        _clear_generating_marker(key, scope)


def async_generate(
    kind: str,
    prompt: str,
    *,
    seed: Optional[int] = None,
    scope: Optional[str] = None,
    force: bool = False,
) -> dict:
    """Enqueue an image generation and return IMMEDIATELY with a cache handle.

    Fast path (always <500ms, no network on the calling thread):
    - On a content-hash cache HIT, returns the cached descriptor right away
      (``status="ready"``, ``cache_hit=True``) — nothing to enqueue.
    - When the _private ingest already provides SERVABLE art for ``scope`` (and not
      ``force``), returns ``status="ingested"`` WITHOUT spending — the viewer serves the
      ingested asset ahead of any generated one anyway, so generating would be pure spend
      for a face that's never displayed (F11-6).
    - On a MISS, spawns a background worker to do the real generate() (provider +
      cache write) and returns a ``status="pending"`` descriptor carrying the
      ``scope`` + ``hash`` the viewer keys off. The image lands in the cache when
      the worker finishes; the viewer's /image endpoint 404→placeholder until then.

    The return ALWAYS includes the keys existing callers read — ``provider``,
    ``kind``, ``prompt``, ``seed``, ``placeholder`` — so it is a drop-in for the old
    synchronous ``generate()`` return on the fire-and-forget DM path. ``status`` and
    ``hash`` are additive (new keys; nothing pre-existing is removed or repurposed).
    ``force`` (additive, default False) bypasses the catalog consult to regenerate
    even when ingested art exists.

    Provider selection happens here (cheap: env read + a lazy client import for
    ``configured()`` — no network), so the cache ``hash`` matches what the worker's
    ``generate()`` will write, including the graceful degrade-to-null name when a
    hosted provider is named but unconfigured.
    """
    provider = get_provider()
    pname = getattr(provider, "name", "null")
    key = content_hash(kind, prompt, seed=seed, provider=pname)

    # Cache hit -> hand it straight back; no worker, no wait.
    hit = cache_read(key, scope)
    if hit is not None:
        # F11-5: a payload-bearing hit (provider lane wrote inline bytes_b64) must NOT
        # be returned verbatim — a single hit would inject ~1-5M chars of base64 into the
        # DM's beat. Strip the inline bytes to METADATA-ONLY (has_bytes/byte_len) for the
        # tool-side return; the on-disk cache entry is untouched, so the viewer's /image
        # endpoint still serves the bytes. No tool-side consumer reads bytes_b64.
        hit = _strip_inline_bytes(hit)
        hit["cache_hit"] = True
        hit.setdefault("status", "ready")
        return hit

    # F11-6: catalog consult. The viewer serves ingested _private art ahead of any
    # generated cache, so if the ingest already has servable art for this scope,
    # generating is pure spend for a face that never displays. Skip (unless force).
    if not force and has_ingested_art(scope):
        return {
            "provider": pname,
            "kind": _normalize_kind(kind),
            "prompt": prompt,
            "seed": seed,
            "placeholder": False,   # the viewer has servable ingested art for this scope
            "status": "ingested",   # additive: "served by the catalog, not generated"
            "hash": key,
            "scope": scope,
            "cache_hit": False,
            "already_pending": False,
        }

    # F11-3: detached-resolver path. A daemon thread dies with the per-beat `claude -p`
    # process (the engine MCP server is its stdio child), so fire-and-forget art started
    # late in a beat is silently lost AND the paid image is never claimed. When enabled,
    # spawn a process-group-detached resolver that outlives the parent and writes the
    # derived cache itself. A young `generating` marker suppresses a re-POST (double spend).
    if _detached_resolver_enabled():
        spawned = _spawn_detached_resolver(kind, prompt, seed, scope, key, pname)
        return {
            "provider": pname,
            "kind": _normalize_kind(kind),
            "prompt": prompt,
            "seed": seed,
            "placeholder": True,
            "status": "pending",
            "hash": key,
            "scope": scope,
            "cache_hit": False,
            # already_pending: a young `generating` marker meant we suppressed a re-spawn.
            "already_pending": not spawned,
            "detached": True,  # additive: resolver runs in a detached subprocess
        }

    # Miss -> enqueue exactly one worker per (key, scope) and return a pending handle.
    with _inflight_lock:
        already = (key, scope) in _inflight
        if not already:
            _inflight.add((key, scope))
    if not already:
        t = threading.Thread(
            target=_worker,
            args=(kind, prompt, seed, scope, key),
            name=f"imagegen-{key[:8]}",
            daemon=True,
        )
        t.start()

    return {
        "provider": pname,
        "kind": _normalize_kind(kind),
        "prompt": prompt,
        "seed": seed,
        "placeholder": True,   # nothing servable YET — viewer shows its placeholder
        "status": "pending",   # additive: signals "enqueued, not yet in cache"
        "hash": key,           # the content-hash the descriptor will be cached under
        "scope": scope,        # the cache scope the viewer fetches via /image?scope=…
        "cache_hit": False,
        "already_pending": already,  # additive: a worker for this key was already running
    }


# --------------------------------------------------------------------------- #
# Detached-resolver CLI entrypoint (F11-3). Invoked ONLY as a subprocess by
# _spawn_detached_resolver: `python imagegen.py --resolve <json-spec>`. Not used by
# the test suite directly (tests call _resolve_entry); not a manual tool.
# --------------------------------------------------------------------------- #

def _main(argv: Optional[list] = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "--resolve":
        try:
            spec = json.loads(args[1])
        except (ValueError, TypeError):
            return 2
        if not isinstance(spec, dict):
            return 2
        # The child inherits the parent's env (state dir, provider, gateway config). As a
        # belt-and-braces fallback, seed the state dir from the spec if the env didn't
        # carry it, so the resolver writes the cache where the viewer reads it.
        sd = spec.get("state_dir")
        if sd and not (env_var("STATE_DIR")):
            os.environ["WORLDOS_STATE_DIR"] = str(sd)
        return _resolve_entry(spec)
    return 2


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    raise SystemExit(_main())
