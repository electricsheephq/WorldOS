"""Minimal client for generating images via the LOCAL OpenClaw gateway.

This rides the gateway's built-in `image_generate` tool (model `openai/gpt-image-2`)
and its existing ChatGPT/Codex OAuth profile (`openai-codex`) — so WorldOS never
needs a raw OpenAI API key of its own. The gateway holds the credential; we just
ask it to draw.

Transport (stdlib only — no new dependency)
-------------------------------------------
We call the gateway's **always-on** HTTP tool-invoke endpoint:

    POST http://127.0.0.1:18789/tools/invoke
    Authorization: Bearer <gateway-token>
    Content-Type: application/json
    {"tool": "image_generate", "args": {"action": "generate", "prompt": "..."}}

This endpoint is enabled by default on the same port as the gateway WS, gated by
the gateway's own auth + tool policy (verified against the installed source at
`dist/tools-invoke-http-*.js` and the docs at
`docs/gateway/tools-invoke-http-api.md`). We do NOT use the WebSocket RPC client
(would need a `websocket`/`websockets` dependency the engine does not have) nor
the admin-HTTP-RPC plugin (off by default, and it does not allowlist
`image_generate` anyway).

Async reality (important)
-------------------------
`image_generate` runs **asynchronously** whenever a session key is present, and
`/tools/invoke` always resolves one (it defaults to the gateway's main session;
verified in `dist/tools-invoke-shared-*.js` `resolveSessionKey`). So the invoke
response is a *started* descriptor carrying a `taskId` — NOT the image bytes:

    {"ok": true, "result": {"content": [{"type":"text","text":"Background task
     started for image generation (task_…). …"}],
     "details": {"action":"generate","status":"started",
                 "task":{"taskId":"task_…"}, …}}}

The finished image is saved on the **gateway host filesystem** at
`<openclaw-config-dir>/media/tool-image-generation/<uuid>.<ext>` (verified in
`dist/store-*.js` `saveMediaBuffer` / `resolveMediaDir`), and the completing
agent normally forwards it via the `message` tool. The task ledger that would let
us read the saved path back (`tasks.get`) is a WS-RPC / admin-HTTP-RPC method,
not a tool, so it is not reachable over the always-on `/tools/invoke` surface.

Given that, this client's retrieval strategy over the always-on surface is:

  1. POST `image_generate` and read the returned `taskId`.
  2. Watch the host media dir for a NEW image file that appears after the
     request started, up to a poll budget. Return its path (and, if small
     enough, its bytes).

If the install runs the gateway on a different host than WorldOS, the media dir
is not local and step (2) cannot see the file; in that case the caller gets the
`taskId` back and `image_path=None`. The provider layer treats "no retrievable
image" as a clean failure so it can fall back to null rather than hang.

We also defensively parse a *synchronous* result shape (`paths` / `attachments`
/ `url` / base64) in case a future gateway returns the image inline (the no
session-key code path in `dist/openclaw-tools-*.js` already does exactly that).

Everything here is stdlib (`urllib`, `json`, `os`, `pathlib`, `time`). No
network in any import path — connections happen only inside `generate_image`.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from _env import env_var_legacy

# --------------------------------------------------------------------------- #
# Defaults / env knobs. All overridable so the selftest and tests can redirect
# without touching the live gateway.
# --------------------------------------------------------------------------- #

# Gateway tool-invoke endpoint. Same port as the gateway WS (18789 default).
DEFAULT_GATEWAY_URL = "http://127.0.0.1:18789"
ENV_GATEWAY_URL = "WORLDOS_OPENCLAW_GATEWAY_URL"
# Bearer token for gateway.auth.mode="token"/"password". REQUIRED for a live call.
ENV_GATEWAY_TOKEN = "WORLDOS_OPENCLAW_GATEWAY_TOKEN"
# OpenClaw also reads these for the same secret; accept them as fallbacks so an
# operator doesn't have to duplicate the token.
ENV_OPENCLAW_TOKEN = "OPENCLAW_GATEWAY_TOKEN"
ENV_OPENCLAW_PASSWORD = "OPENCLAW_GATEWAY_PASSWORD"
# Model ref the gateway should use. Codex OAuth uses the same gpt-image-2 ref.
ENV_MODEL = "WORLDOS_OPENCLAW_IMAGE_MODEL"
DEFAULT_MODEL = "openai/gpt-image-2"
# Where the gateway saves generated images on the host (config dir + /media/...).
ENV_MEDIA_DIR = "WORLDOS_OPENCLAW_MEDIA_DIR"
ENV_OPENCLAW_HOME = "OPENCLAW_HOME"  # OpenClaw's own config-dir override.
MEDIA_SUBDIR = ("media", "tool-image-generation")

# Connection + poll budget (seconds). Image gen is slow; the gateway's own
# default is ~120-180s. We keep the connect timeout short (fail fast if the
# gateway is down) but allow a longer completion poll.
ENV_CONNECT_TIMEOUT = "WORLDOS_OPENCLAW_CONNECT_TIMEOUT"
DEFAULT_CONNECT_TIMEOUT = 5.0
ENV_POLL_TIMEOUT = "WORLDOS_OPENCLAW_POLL_TIMEOUT"
DEFAULT_POLL_TIMEOUT = 180.0
POLL_INTERVAL = 1.0
# Don't slurp giant files into memory; above this we return path-only.
MAX_INLINE_BYTES = 16 * 1024 * 1024

# Image file extensions the gateway may emit (png/jpeg/webp).
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# --------------------------------------------------------------------------- #
# F11-4: media-dir cross-attribution guard.
#
# The media dir is SHARED — the skill mandates scene + portrait art in one beat, so two
# generations run concurrently (one per (key,scope); imagegen spawns a thread/resolver
# each, with no cross-key serialization). Both snapshot the dir, both poll, and when the
# first file lands BOTH see it as "fresh" and `max(fresh, key=mtime)` returns the SAME
# path -> two scopes claim one image (and any unrelated gateway image task is claimable).
#
# Two-part fix, derived-cache-only and invariant-safe:
#   1. A module-level GENERATION LOCK serializes the POST->claim window so concurrent
#      generations don't overlap their claim races. Serialization is off the DM turn
#      (async), so the latency cost is invisible to play.
#   2. A module-level CLAIMED-PATH set: every path a generation claims is recorded, and a
#      claim picks the OLDEST unclaimed fresh file (FIFO drop order) — so even a file that
#      lands during another generation's window can never be double-claimed.
# Both are best-effort within one process; they hold for the in-process burst that is the
# real-world case (one engine process spawning scene+portrait in a beat).
# --------------------------------------------------------------------------- #
_GENERATION_LOCK = threading.Lock()
_CLAIMED_LOCK = threading.Lock()
_CLAIMED_PATHS: set[str] = set()

# Suffix of the sidecar lock file that records a CROSS-PROCESS claim (F11-3/F11-4): the
# detached resolver runs in a separate process, so an in-memory set can't serialize two
# resolvers. An atomic O_CREAT|O_EXCL `<image>.claimed` file does — the first resolver to
# create it owns the image; a second resolver's create fails and it skips to the next file.
_CLAIM_SUFFIX = ".claimed"


def _claim_lockfile(path: Path) -> Path:
    return path.with_name(path.name + _CLAIM_SUFFIX)


def _claim_path(path: Path) -> bool:
    """Atomically claim `path` for THIS generation. Returns True if we won the claim,
    False if another generation (in this process OR a separate detached resolver) already
    owns it. Two layers: an in-memory set for the in-process burst, and an exclusive
    `<image>.claimed` lock file for cross-process resolvers. The lock file is a derived,
    rebuildable artifact next to the (derived) media file — never campaign state."""
    key = str(path)
    with _CLAIMED_LOCK:
        if key in _CLAIMED_PATHS:
            return False
        _CLAIMED_PATHS.add(key)
    # Cross-process exclusive claim. O_CREAT|O_EXCL is atomic on a local fs: exactly one
    # creator wins. If we can't even attempt it (no parent dir, perms), fall back to the
    # in-memory claim we already hold — no worse than today within a single process.
    import os
    lock = _claim_lockfile(path)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # Another resolver process already owns it — release our in-memory claim so a
        # later, distinct file can still be claimed by us.
        with _CLAIMED_LOCK:
            _CLAIMED_PATHS.discard(key)
        return False
    except OSError:
        return True  # best-effort: keep the in-memory claim (single-process correctness)
    else:
        os.close(fd)
        return True


def _is_claimed(path: Path) -> bool:
    """True if `path` is already claimed in-process OR a cross-process `.claimed` lock
    exists for it (so a poll loop skips a file another resolver owns)."""
    with _CLAIMED_LOCK:
        if str(path) in _CLAIMED_PATHS:
            return True
    return _claim_lockfile(path).exists()


def _reset_claimed_paths() -> None:
    """Test helper: clear the in-memory claimed-path registry between cases. (The
    on-disk `.claimed` lock files live under each test's tmp media dir, so they're
    isolated by tmp_path and need no global reset.)"""
    with _CLAIMED_LOCK:
        _CLAIMED_PATHS.clear()


class OpenClawImageError(RuntimeError):
    """Any failure talking to the gateway or retrieving the image.

    Raised so the provider layer can catch it and fall back to null instead of
    hanging or crashing the server.
    """


class OpenClawGatewayUnreachable(OpenClawImageError):
    """The gateway could not be reached (connection refused / timed out / DNS).

    Distinct subclass so the provider/tests can assert the unreachable→raise
    path specifically.
    """


@dataclass
class ImageResult:
    """What a generation produced.

    At least one of `path`/`url`/`data` is set on success. `task_id` is the
    gateway background-task id (present for the async path). `raw` keeps the
    gateway's tool result for debugging/caching.
    """

    task_id: Optional[str] = None
    path: Optional[str] = None
    url: Optional[str] = None
    data: Optional[bytes] = None
    mime_type: Optional[str] = None
    raw: dict = field(default_factory=dict)

    def has_image(self) -> bool:
        return bool(self.path or self.url or self.data)


@dataclass
class OpenClawImageClient:
    """Stdlib HTTP client for the gateway's `image_generate` tool.

    Construction is cheap and reads no secrets eagerly beyond resolving config
    from env (so the provider can probe `configured()` freely). The token is
    only *required* when `generate_image` actually fires.
    """

    gateway_url: str = ""
    token: Optional[str] = None
    model: str = ""
    media_dir: Optional[Path] = None
    connect_timeout: float = 0.0
    poll_timeout: float = 0.0
    # Pre-POST media-dir snapshot, stashed by _post_generate for the claim step. Not a
    # config knob — instance scratch state for one generate_image call.
    _existing_snapshot: set = field(default_factory=set, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.gateway_url = (self.gateway_url or _env(ENV_GATEWAY_URL, DEFAULT_GATEWAY_URL)).rstrip("/")
        if self.token is None:
            self.token = (
                _env(ENV_GATEWAY_TOKEN, "")
                or _env(ENV_OPENCLAW_TOKEN, "")
                or _env(ENV_OPENCLAW_PASSWORD, "")
                or None
            )
        self.model = self.model or _env(ENV_MODEL, DEFAULT_MODEL)
        if self.media_dir is None:
            self.media_dir = _resolve_media_dir()
        else:
            self.media_dir = Path(self.media_dir)
        if not self.connect_timeout:
            self.connect_timeout = _env_float(ENV_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT)
        if not self.poll_timeout:
            self.poll_timeout = _env_float(ENV_POLL_TIMEOUT, DEFAULT_POLL_TIMEOUT)

    # -- request building -------------------------------------------------- #

    @property
    def invoke_url(self) -> str:
        return f"{self.gateway_url}/tools/invoke"

    def build_request_body(
        self,
        prompt: str,
        *,
        size: Optional[str] = None,
        count: int = 1,
    ) -> dict:
        """The exact JSON body POSTed to /tools/invoke.

        Mirrors the documented tool-invoke shape: top-level `tool` + `args`, with
        the image params nested under `args` (action/prompt/model/size/count).
        Kept as a pure function so the unit test can assert it without any I/O.
        """
        args: dict = {"action": "generate", "prompt": prompt, "model": self.model}
        if size:
            args["size"] = size
        if count and count != 1:
            args["count"] = count
        return {"tool": "image_generate", "args": args}

    def _build_urllib_request(self, body: dict) -> urllib.request.Request:
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(self.invoke_url, data=data, headers=headers, method="POST")

    # -- transport (overridable seam for tests) ---------------------------- #

    def _post(self, body: dict) -> dict:
        """POST the body to /tools/invoke and return the parsed JSON envelope.

        Raises OpenClawGatewayUnreachable on connection failure/timeout, and
        OpenClawImageError on HTTP/auth/tool errors. Tests monkeypatch this (or
        `_open`) to mock the transport — no real socket is opened in tests.
        """
        req = self._build_urllib_request(body)
        try:
            raw = self._open(req)
        except urllib.error.HTTPError as exc:  # 4xx/5xx — read the error body.
            detail = _safe_read(exc)
            if exc.code in (401, 403):
                raise OpenClawImageError(
                    f"gateway rejected the image_generate call ({exc.code}): "
                    f"check {ENV_GATEWAY_TOKEN}. {detail}"
                ) from exc
            if exc.code == 404:
                raise OpenClawImageError(
                    "gateway has no image_generate tool available (404) — no "
                    "image provider configured, or the tool is denied by policy. "
                    f"{detail}"
                ) from exc
            raise OpenClawImageError(f"gateway image_generate HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            # URLError wraps connection-refused / DNS / timeout. The owner's live
            # "Eva" gateway being down lands here -> distinct unreachable error.
            raise OpenClawGatewayUnreachable(
                f"OpenClaw gateway unreachable at {self.invoke_url}: {exc}. "
                "Is the local gateway running?"
            ) from exc
        try:
            envelope = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise OpenClawImageError(f"gateway returned non-JSON response: {exc}") from exc
        if not isinstance(envelope, dict):
            raise OpenClawImageError("gateway returned an unexpected (non-object) response")
        return envelope

    def _open(self, req: urllib.request.Request) -> bytes:
        """The single real network call, isolated so tests can stub it cleanly."""
        with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
            return resp.read()

    # -- public API -------------------------------------------------------- #

    def generate_image(
        self,
        prompt: str,
        *,
        size: Optional[str] = None,
        count: int = 1,
        wait: bool = True,
    ) -> ImageResult:
        """Ask the gateway to generate an image for `prompt`.

        Returns an ImageResult. On the (normal) async path it carries `task_id`,
        and — if `wait` and the gateway's media dir is local and visible — also
        the saved file `path` (plus bytes when small). On a synchronous gateway
        result it carries the inline path/url/data directly.

        Raises OpenClawGatewayUnreachable if the gateway can't be reached, and
        OpenClawImageError for auth/policy/protocol failures, so the caller can
        fall back to null. Never blocks beyond `poll_timeout`.
        """
        if not (prompt or "").strip():
            raise OpenClawImageError("prompt is required for image generation")

        # F11-4: serialize the POST->claim window across concurrent generations so two
        # don't race for the same landed file. Only the waiting (claim-bearing) path needs
        # the lock; wait=False just fires the POST and returns the task id, no claim. The
        # `with` is a no-op for wait=False (we acquire nothing).
        if not wait:
            return self._generate_post_only(prompt, size=size, count=count)
        with _GENERATION_LOCK:
            return self._generate_and_claim(prompt, size=size, count=count)

    def _generate_post_only(self, prompt: str, *, size: Optional[str], count: int) -> "ImageResult":
        """POST and return the started descriptor (task id) without watching for a file."""
        out, _started = self._post_generate(prompt, size=size, count=count)
        return out

    def _generate_and_claim(self, prompt: str, *, size: Optional[str], count: int) -> "ImageResult":
        """POST, then watch the media dir and claim the oldest unclaimed fresh file."""
        out, started_at = self._post_generate(prompt, size=size, count=count, snapshot=True)
        if out.has_image():
            return out
        found = self._await_new_media(self._existing_snapshot, since=started_at)
        if found is not None:
            out.path = str(found)
            out.mime_type = _mime_for(found)
            if found.stat().st_size <= MAX_INLINE_BYTES:
                out.data = found.read_bytes()
            return out
        # No file appeared in budget (slow gen, or gateway is on another host
        # so its media dir isn't visible here). Clean failure -> fall back.
        raise OpenClawImageError(
            "image_generate task started"
            + (f" ({out.task_id})" if out.task_id else "")
            + f" but no image appeared in {self.media_dir} within "
            f"{self.poll_timeout:.0f}s. The gateway may be remote, slow, or "
            "still rendering; retrieve the image from the gateway host or its "
            "chat session."
        )

    def _post_generate(self, prompt: str, *, size: Optional[str], count: int,
                       snapshot: bool = False) -> tuple["ImageResult", float]:
        """Shared POST + envelope parse. Returns (ImageResult, started_at). When
        `snapshot` is set, the pre-POST media-dir set is stashed on the instance so the
        claim step excludes it. `started_at` is captured BEFORE the snapshot so a file the
        gateway writes during the POST is counted as fresh."""
        started_at = time.time()
        self._existing_snapshot = self._snapshot_media_dir() if snapshot else set()

        body = self.build_request_body(prompt, size=size, count=count)
        envelope = self._post(body)

        if not envelope.get("ok", False):
            err = envelope.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise OpenClawImageError(f"gateway image_generate failed: {msg or 'unknown error'}")

        result = envelope.get("result")
        if not isinstance(result, dict):
            raise OpenClawImageError("gateway image_generate returned no result payload")

        out = ImageResult(raw=result)
        # Synchronous result shape (no session key path): paths/attachments/url/data.
        _extract_inline_image(result, out)
        out.task_id = _extract_task_id(result)
        return out, started_at

    # -- media-dir watching ------------------------------------------------ #

    def _snapshot_media_dir(self) -> set:
        """Set of image paths currently in the media dir (empty if it doesn't exist)."""
        d = self.media_dir
        if not d or not d.is_dir():
            return set()
        return {p for p in d.iterdir() if p.suffix.lower() in _IMAGE_EXTS}

    def _await_new_media(self, before: set, *, since: float) -> Optional[Path]:
        """Poll the media dir until a NEW, UNCLAIMED image file appears (F11-4).

        "New" = a path not present in `before` (the pre-POST snapshot) and modified
        at/after `since`. Among such files we claim the OLDEST unclaimed one (FIFO in
        drop order) and atomically record the claim, so a concurrent generation can never
        take the same path. Returns the claimed file, or None on timeout / no-such-dir.
        """
        d = self.media_dir
        if not d:
            return None
        deadline = since + self.poll_timeout
        while time.time() < deadline:
            if d.is_dir():
                current = {p for p in d.iterdir() if p.suffix.lower() in _IMAGE_EXTS}
                fresh = [
                    p
                    for p in current - before
                    if _safe_mtime(p) >= since - 1.0  # 1s slop for clock/fs granularity
                    and not _is_claimed(p)            # F11-4: skip another scope's image
                ]
                if fresh:
                    # FIFO: claim the OLDEST unclaimed fresh file, so when scene+portrait
                    # land in order the first generation takes the first file. Tie-break on
                    # path so two files with identical mtime resolve deterministically.
                    fresh.sort(key=lambda p: (_safe_mtime(p), str(p)))
                    for cand in fresh:
                        if _claim_path(cand):  # we won the claim (lost the race -> next)
                            return cand
            time.sleep(POLL_INTERVAL)
        return None


# --------------------------------------------------------------------------- #
# Module-level helpers (pure-ish; importing this module touches no network).
# --------------------------------------------------------------------------- #

def _env(name: str, default: str) -> str:
    # WORLDOS_OPENCLAW_* names resolve via the WORLDOS_* alias (warn-only legacy
    # fallback); OPENCLAW_* names pass through unchanged. See issue #295 (W0-E).
    return env_var_legacy(name, default) or default


def _env_float(name: str, default: float) -> float:
    try:
        v = env_var_legacy(name)
        return float(v) if v else default
    except (TypeError, ValueError):
        return default


def _resolve_media_dir() -> Path:
    """Resolve the gateway's host media dir: <config-dir>/media/tool-image-generation.

    Honors an explicit override (WORLDOS_OPENCLAW_MEDIA_DIR), then OpenClaw's own
    OPENCLAW_HOME, then the conventional ~/.openclaw. Matches the install's
    `resolveMediaDir()` (config dir + "media") plus the tool's "tool-image-generation"
    subdir.
    """
    override = env_var_legacy(ENV_MEDIA_DIR)
    if override:
        return Path(override)
    home = env_var_legacy(ENV_OPENCLAW_HOME)
    config_dir = Path(home) if home else (Path.home() / ".openclaw")
    return config_dir.joinpath(*MEDIA_SUBDIR)


def _extract_task_id(result: dict) -> Optional[str]:
    """Pull the background task id out of the tool result, tolerating shapes.

    The started result nests it as details.task.taskId; we also accept
    details.taskId and a top-level taskId defensively.
    """
    details = result.get("details")
    if isinstance(details, dict):
        task = details.get("task")
        if isinstance(task, dict) and task.get("taskId"):
            return str(task["taskId"])
        if details.get("taskId"):
            return str(details["taskId"])
    if result.get("taskId"):
        return str(result["taskId"])
    return None


def _extract_inline_image(result: dict, out: "ImageResult") -> None:
    """Populate `out` from a *synchronous* tool result if one is present.

    Handles the inline shapes the gateway uses on its no-session path:
      details.paths: [host paths]
      details.media.mediaUrls: [host paths]
      details.attachments: [{path|url, mimeType}]
      details.media.attachments: [...]
    plus a base64 `data`/`b64`/`image` field if a provider ever returns bytes.
    """
    details = result.get("details")
    if not isinstance(details, dict):
        return

    media = details.get("media") if isinstance(details.get("media"), dict) else {}

    # Attachments (preferred — carry mime + path/url).
    for bucket in (details.get("attachments"), media.get("attachments")):
        if isinstance(bucket, list):
            for att in bucket:
                if not isinstance(att, dict):
                    continue
                if att.get("path") and not out.path:
                    out.path = str(att["path"])
                    out.mime_type = out.mime_type or att.get("mimeType")
                url = att.get("url") or att.get("mediaUrl") or att.get("fileUrl")
                if url and not out.url and _is_http_url(url):
                    out.url = str(url)

    # Plain path/url lists.
    for key_src in (details.get("paths"), media.get("mediaUrls")):
        if isinstance(key_src, list):
            for item in key_src:
                if not isinstance(item, str):
                    continue
                if _is_http_url(item):
                    out.url = out.url or item
                elif not out.path:
                    out.path = item

    # Raw base64 bytes, if any provider surfaces them.
    for key in ("data", "b64", "b64_json", "image"):
        val = details.get(key)
        if isinstance(val, str) and val and not out.data:
            decoded = _maybe_b64(val)
            if decoded is not None:
                out.data = decoded


def _is_http_url(value: object) -> bool:
    return isinstance(value, str) and (value.startswith("http://") or value.startswith("https://"))


def _maybe_b64(value: str) -> Optional[bytes]:
    raw = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    try:
        # binascii.Error (raised by b64decode) subclasses ValueError, so this
        # single except covers both malformed-padding and non-base64 input.
        return base64.b64decode(raw, validate=True)
    except ValueError:
        return None


def _mime_for(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _safe_read(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # pragma: no cover - defensive
        return ""
    return body[:500]


# --------------------------------------------------------------------------- #
# Manual selftest — fires ONE real generation against the live gateway.
#
# NOT run by the test suite or by the build. Run it yourself when you want to
# verify end-to-end against your own gateway:
#
#   export WORLDOS_OPENCLAW_GATEWAY_TOKEN=<your gateway token>
#   uv run --directory servers/engine python openclaw_image.py --selftest \
#       --prompt "a mossy stone dungeon door, torchlit, painterly"
#
# It spends one real image call on your OAuth/Codex budget and writes nothing
# except whatever the gateway already saves to its media dir. Override the
# gateway with --gateway-url / WORLDOS_OPENCLAW_GATEWAY_URL if needed.
# --------------------------------------------------------------------------- #

def _selftest(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Fire one real OpenClaw image_generate call.")
    parser.add_argument("--selftest", action="store_true", help="Run the live selftest.")
    parser.add_argument("--prompt", default="a friendly tavern keeper, fantasy portrait, painterly")
    parser.add_argument("--gateway-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--size", default=None, help="e.g. 1024x1024")
    parser.add_argument("--no-wait", action="store_true", help="Return the task id without waiting.")
    args = parser.parse_args(argv)

    if not args.selftest:
        parser.print_help()
        return 2

    client = OpenClawImageClient(
        gateway_url=args.gateway_url or "",
        token=args.token,
        model=args.model or "",
    )
    print(f"POST {client.invoke_url}  model={client.model}")
    print(f"media dir watched: {client.media_dir}")
    if not client.token:
        print(
            f"WARNING: no token set ({ENV_GATEWAY_TOKEN}); the call will likely 401 "
            "if the gateway uses token/password auth."
        )
    try:
        res = client.generate_image(args.prompt, size=args.size, wait=not args.no_wait)
    except OpenClawGatewayUnreachable as exc:
        print(f"UNREACHABLE: {exc}")
        return 1
    except OpenClawImageError as exc:
        print(f"FAILED: {exc}")
        return 1

    print(f"task_id : {res.task_id}")
    print(f"path    : {res.path}")
    print(f"url     : {res.url}")
    print(f"bytes   : {len(res.data) if res.data else 0}")
    print(f"mime    : {res.mime_type}")
    return 0 if (res.has_image() or res.task_id) else 1


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(_selftest())
