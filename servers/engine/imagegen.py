"""Swappable image-generation layer for ClawDnD.

Mirrors the voice TTS/STT design (see ../voice/interface.py, ../voice/stt.py): the
caller asks for one thing, generate(kind, prompt), and a provider selected by
CLAWDND_IMAGE_PROVIDER turns that request into an image descriptor. Providers are
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
    raises NotImplementedError with a clear "set CLAWDND_IMAGE_* to wire" message.
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
import time
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

import store

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
    api_key_env = "CLAWDND_IMAGE_API_KEY"

    def configured(self) -> bool:
        """True once the credential env var is set. get_provider() uses this to
        decide whether to hand back this provider or degrade to null."""
        return bool(os.environ.get(self.api_key_env, "").strip())

    def generate(self, kind: str, prompt: str, *, seed: Optional[int] = None) -> dict:
        raise NotImplementedError(
            f"{self.name} image provider not implemented yet — "
            f"set CLAWDND_IMAGE_PROVIDER=null for now, or wire this backend and "
            f"set {self.api_key_env} (plus CLAWDND_IMAGE_MODEL) to enable it."
        )


class OpenAIImageProvider(_UnconfiguredHostedProvider):
    """OpenAI image generation (stub seam).

    Real wiring would call the Images API (e.g. gpt-image-1 / DALL·E) over HTTPS
    using stdlib urllib or an optional client in an `image-openai` dependency
    group, read the model from CLAWDND_IMAGE_MODEL, and return {"bytes": ...} or
    {"url": ...} for generate() to cache. Needs CLAWDND_IMAGE_API_KEY.
    """

    name = "openai"
    api_key_env = "CLAWDND_IMAGE_API_KEY"


class StabilityImageProvider(_UnconfiguredHostedProvider):
    """Stability AI image generation (stub seam).

    Real wiring would call the Stability REST API (e.g. SDXL) over HTTPS, read the
    model/engine from CLAWDND_IMAGE_MODEL, and return image bytes for generate() to
    cache. Needs CLAWDND_IMAGE_API_KEY.
    """

    name = "stability"
    api_key_env = "CLAWDND_IMAGE_API_KEY"


class OpenClawImageProvider:
    """Generate images through the LOCAL OpenClaw gateway's `image_generate` tool.

    Unlike the OpenAI/Stability stubs, this one is REAL — but it carries no API key
    of its own. It rides the gateway's built-in image generation (model
    `openai/gpt-image-2`) and the gateway's existing ChatGPT/Codex OAuth profile,
    so the credential lives in the gateway, not here. See `openclaw_image.py` for
    the transport (stdlib HTTP against the always-on `/tools/invoke` endpoint) and
    the async/host-filesystem retrieval details.

    Selection: `CLAWDND_IMAGE_PROVIDER=openclaw`. The gateway URL/token/model are
    read from `CLAWDND_OPENCLAW_*` env (with `OPENCLAW_GATEWAY_TOKEN` /
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
        this is safe to probe during selection. Token from CLAWDND_OPENCLAW_GATEWAY_TOKEN
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


# Real, hosted providers keyed by CLAWDND_IMAGE_PROVIDER value.
_HOSTED: dict[str, type] = {
    "openai": OpenAIImageProvider,
    "stability": StabilityImageProvider,
    "openclaw": OpenClawImageProvider,
}


def provider_name() -> str:
    """The selected image provider name (env CLAWDND_IMAGE_PROVIDER, default 'null')."""
    return os.environ.get("CLAWDND_IMAGE_PROVIDER", "null").strip().lower()


def get_provider() -> ImageProvider:
    """Construct the image provider selected by CLAWDND_IMAGE_PROVIDER.

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
        return descriptor

    descriptor["cache_hit"] = False
    if use_cache:
        cache_write(descriptor, scope)

    return descriptor
