"""OpenClaw image-provider tests — mocked RPC transport, no network, no gateway.

Covers the three required behaviors plus the async retrieval path:

  1. Request building — the exact /tools/invoke body for image_generate.
  2. Cache hit — a second generate() for the same request is served from the
     content-hash cache (the provider is invoked exactly once).
  3. Unreachable -> raise — a connection failure surfaces as a clean typed error,
     and through imagegen.generate() as a RuntimeError the caller can catch.

The transport is mocked by stubbing OpenClawImageClient._open (the single real
urlopen) or _post — never a real socket. The async path is exercised by pointing
the client's media_dir at a tmp dir and dropping a file into it.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

import imagegen
import openclaw_image
from openclaw_image import (
    ImageResult,
    OpenClawGatewayUnreachable,
    OpenClawImageClient,
    OpenClawImageError,
)


# --------------------------------------------------------------------------- #
# Helpers: a fake _open that returns a canned gateway envelope, and the two
# canonical result shapes (async "started" + synchronous inline).
# --------------------------------------------------------------------------- #

def _started_envelope(task_id: str = "task_abc123") -> dict:
    """The async 'started' tool result the gateway returns via /tools/invoke."""
    return {
        "ok": True,
        "result": {
            "content": [{"type": "text", "text": f"Background task started ({task_id})."}],
            "details": {
                "action": "generate",
                "status": "started",
                "task": {"taskId": task_id},
            },
        },
    }


def _inline_envelope(path: str = "/srv/.openclaw/media/tool-image-generation/img.png") -> dict:
    """A synchronous tool result (the gateway's no-session path) with paths inline."""
    return {
        "ok": True,
        "result": {
            "content": [{"type": "text", "text": "Generated 1 image."}],
            "details": {
                "provider": "openai",
                "model": "openai/gpt-image-2",
                "count": 1,
                "paths": [path],
                "media": {"mediaUrls": [path], "attachments": [{"type": "image", "path": path, "mimeType": "image/png"}]},
                "attachments": [{"type": "image", "path": path, "mimeType": "image/png"}],
            },
        },
    }


def _stub_open(envelope: dict):
    """Return a function suitable for monkeypatching OpenClawImageClient._open."""

    def _open(self, req):  # noqa: ANN001 - test stub
        return json.dumps(envelope).encode("utf-8")

    return _open


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Keep every test off the real gateway and the real media dir.

    Always provide a token (so the client doesn't no-op on auth) and point the
    media dir at a tmp dir, and clear provider selection by default.
    """
    monkeypatch.setenv("CLAWDND_OPENCLAW_GATEWAY_TOKEN", "test-token")
    monkeypatch.setenv("CLAWDND_OPENCLAW_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.delenv("CLAWDND_IMAGE_PROVIDER", raising=False)
    # Tight poll budget so the no-image timeout test is fast.
    monkeypatch.setenv("CLAWDND_OPENCLAW_POLL_TIMEOUT", "0.5")
    return tmp_path


# --------------------------------------------------------------------------- #
# 1) Request building.
# --------------------------------------------------------------------------- #

def test_build_request_body_default():
    client = OpenClawImageClient(gateway_url="http://127.0.0.1:18789", token="t", model="openai/gpt-image-2")
    body = client.build_request_body("a torchlit crypt")
    assert body == {
        "tool": "image_generate",
        "args": {"action": "generate", "prompt": "a torchlit crypt", "model": "openai/gpt-image-2"},
    }


def test_build_request_body_with_size_and_count():
    client = OpenClawImageClient(token="t", model="openai/gpt-image-2")
    body = client.build_request_body("a dragon", size="1024x1024", count=2)
    assert body["args"]["size"] == "1024x1024"
    assert body["args"]["count"] == 2
    # count==1 is omitted (the gateway default) to keep the body minimal.
    assert "count" not in client.build_request_body("x", count=1)["args"]


def test_invoke_url_and_auth_header():
    client = OpenClawImageClient(gateway_url="http://127.0.0.1:18789/", token="secret-123")
    assert client.invoke_url == "http://127.0.0.1:18789/tools/invoke"
    req = client._build_urllib_request({"tool": "image_generate", "args": {}})
    assert req.get_header("Authorization") == "Bearer secret-123"
    assert req.get_header("Content-type") == "application/json"
    assert req.method == "POST"


def test_no_token_omits_auth_header(monkeypatch):
    monkeypatch.delenv("CLAWDND_OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_GATEWAY_PASSWORD", raising=False)
    client = OpenClawImageClient(gateway_url="http://127.0.0.1:18789")
    assert client.token is None
    req = client._build_urllib_request({"tool": "image_generate", "args": {}})
    assert req.get_header("Authorization") is None


def test_token_falls_back_to_openclaw_env(monkeypatch):
    monkeypatch.delenv("CLAWDND_OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "from-openclaw-env")
    assert OpenClawImageClient().token == "from-openclaw-env"


def test_post_sends_built_body(monkeypatch):
    """The body handed to the transport is exactly build_request_body's output."""
    seen = {}

    def _open(self, req):  # noqa: ANN001
        seen["body"] = json.loads(req.data.decode("utf-8"))
        seen["url"] = req.full_url
        return json.dumps(_started_envelope()).encode("utf-8")

    monkeypatch.setattr(OpenClawImageClient, "_open", _open)
    client = OpenClawImageClient(token="t")
    client.generate_image("a goblin warren", wait=False)
    assert seen["url"].endswith("/tools/invoke")
    assert seen["body"]["tool"] == "image_generate"
    assert seen["body"]["args"]["prompt"] == "a goblin warren"
    assert seen["body"]["args"]["action"] == "generate"


# --------------------------------------------------------------------------- #
# Async path: started -> task id, then media-dir watch yields the file.
# --------------------------------------------------------------------------- #

def test_generate_async_returns_task_id_without_wait(monkeypatch):
    monkeypatch.setattr(OpenClawImageClient, "_open", _stub_open(_started_envelope("task_xyz")))
    res = OpenClawImageClient(token="t").generate_image("a lich", wait=False)
    assert res.task_id == "task_xyz"
    assert res.path is None and res.data is None  # async: nothing inline yet.


def test_generate_async_waits_for_media_file(monkeypatch, tmp_path):
    media = tmp_path / "media"
    media.mkdir(parents=True)
    img = media / "deadbeef.png"

    # Simulate the gateway saving the image DURING the invoke (after the client's
    # pre-request snapshot). Writing it from inside _open guarantees it isn't in
    # the `existing` set, so it's correctly detected as fresh output.
    def _open(self, req):  # noqa: ANN001
        img.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
        return json.dumps(_started_envelope("task_wait")).encode("utf-8")

    monkeypatch.setattr(OpenClawImageClient, "_open", _open)
    client = OpenClawImageClient(token="t", media_dir=media, poll_timeout=2.0)

    res = client.generate_image("a beholder")
    assert res.task_id == "task_wait"
    assert res.path == str(img)
    assert res.mime_type == "image/png"
    assert res.data == b"\x89PNG\r\n\x1a\nFAKE"  # small file -> bytes inlined.


def test_generate_async_ignores_preexisting_files(monkeypatch, tmp_path):
    """A file already present BEFORE the request must not be mistaken for output."""
    media = tmp_path / "media"
    media.mkdir(parents=True)
    stale = media / "old.png"
    stale.write_bytes(b"old")
    # Backdate it well before "now" so it's neither in the post-request set nor fresh.
    import os as _os

    _os.utime(stale, (1000, 1000))

    monkeypatch.setattr(OpenClawImageClient, "_open", _stub_open(_started_envelope()))
    client = OpenClawImageClient(token="t", media_dir=media, poll_timeout=0.4)
    with pytest.raises(OpenClawImageError, match="no image appeared"):
        client.generate_image("a wraith")


def test_generate_async_no_image_in_budget_raises(monkeypatch, tmp_path):
    """Slow/remote gateway: nothing lands in the media dir -> clean raise, no hang."""
    media = tmp_path / "media"
    media.mkdir(parents=True)
    monkeypatch.setattr(OpenClawImageClient, "_open", _stub_open(_started_envelope("task_slow")))
    client = OpenClawImageClient(token="t", media_dir=media, poll_timeout=0.3)
    with pytest.raises(OpenClawImageError) as exc:
        client.generate_image("a mind flayer")
    assert "task_slow" in str(exc.value)


# --------------------------------------------------------------------------- #
# Synchronous inline result shape (no-session path / future gateways).
# --------------------------------------------------------------------------- #

def test_generate_parses_inline_path(monkeypatch):
    monkeypatch.setattr(OpenClawImageClient, "_open", _stub_open(_inline_envelope("/x/y/img.png")))
    res = OpenClawImageClient(token="t").generate_image("a kobold")
    assert res.path == "/x/y/img.png"
    assert res.mime_type == "image/png"


def test_generate_parses_inline_http_url(monkeypatch):
    env = _inline_envelope()
    env["result"]["details"] = {
        "attachments": [{"type": "image", "url": "https://cdn.example/x.png", "mimeType": "image/png"}],
    }
    monkeypatch.setattr(OpenClawImageClient, "_open", _stub_open(env))
    res = OpenClawImageClient(token="t").generate_image("a giant")
    assert res.url == "https://cdn.example/x.png"
    assert res.path is None


def test_generate_parses_inline_base64(monkeypatch):
    import base64

    payload = base64.b64encode(b"PNGDATA").decode("ascii")
    env = {"ok": True, "result": {"content": [], "details": {"data": payload}}}
    monkeypatch.setattr(OpenClawImageClient, "_open", _stub_open(env))
    res = OpenClawImageClient(token="t").generate_image("a troll")
    assert res.data == b"PNGDATA"


# --------------------------------------------------------------------------- #
# Error paths: gateway error envelope, HTTP errors, and 3) unreachable -> raise.
# --------------------------------------------------------------------------- #

def test_gateway_error_envelope_raises(monkeypatch):
    env = {"ok": False, "error": {"type": "tool_error", "message": "no provider configured"}}
    monkeypatch.setattr(OpenClawImageClient, "_open", _stub_open(env))
    with pytest.raises(OpenClawImageError, match="no provider configured"):
        OpenClawImageClient(token="t").generate_image("x")


def test_http_401_raises_auth_hint(monkeypatch):
    def _open(self, req):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr(OpenClawImageClient, "_open", _open)
    with pytest.raises(OpenClawImageError) as exc:
        OpenClawImageClient(token="bad").generate_image("x")
    assert "401" in str(exc.value)


def test_http_404_raises_tool_unavailable(monkeypatch):
    def _open(self, req):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(OpenClawImageClient, "_open", _open)
    with pytest.raises(OpenClawImageError, match="no image_generate tool"):
        OpenClawImageClient(token="t").generate_image("x")


def test_unreachable_gateway_raises(monkeypatch):
    """3) Connection refused / DNS / timeout -> OpenClawGatewayUnreachable."""

    def _open(self, req):  # noqa: ANN001
        raise urllib.error.URLError(ConnectionRefusedError("Connection refused"))

    monkeypatch.setattr(OpenClawImageClient, "_open", _open)
    with pytest.raises(OpenClawGatewayUnreachable) as exc:
        OpenClawImageClient(token="t", gateway_url="http://127.0.0.1:18789").generate_image("x")
    assert "unreachable" in str(exc.value).lower()
    # It's a subclass of the base error, so a broad caller catches it too.
    assert isinstance(exc.value, OpenClawImageError)


def test_timeout_is_unreachable(monkeypatch):
    def _open(self, req):  # noqa: ANN001
        raise TimeoutError("timed out")

    monkeypatch.setattr(OpenClawImageClient, "_open", _open)
    with pytest.raises(OpenClawGatewayUnreachable):
        OpenClawImageClient(token="t").generate_image("x")


def test_non_json_response_raises(monkeypatch):
    def _open(self, req):  # noqa: ANN001
        return b"<html>not json</html>"

    monkeypatch.setattr(OpenClawImageClient, "_open", _open)
    with pytest.raises(OpenClawImageError, match="non-JSON"):
        OpenClawImageClient(token="t").generate_image("x")


def test_empty_prompt_raises_before_network(monkeypatch):
    # No transport stub -> if it tried the network the test would fail differently.
    with pytest.raises(OpenClawImageError, match="prompt is required"):
        OpenClawImageClient(token="t").generate_image("   ")


# --------------------------------------------------------------------------- #
# media-dir resolution.
# --------------------------------------------------------------------------- #

def test_media_dir_override(monkeypatch):
    monkeypatch.setenv("CLAWDND_OPENCLAW_MEDIA_DIR", "/custom/media")
    assert str(OpenClawImageClient(token="t").media_dir) == "/custom/media"


def test_media_dir_from_openclaw_home(monkeypatch):
    monkeypatch.delenv("CLAWDND_OPENCLAW_MEDIA_DIR", raising=False)
    monkeypatch.setenv("OPENCLAW_HOME", "/opt/oc-home")
    d = OpenClawImageClient(token="t").media_dir
    assert str(d) == "/opt/oc-home/media/tool-image-generation"


def test_media_dir_default_is_dot_openclaw(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAWDND_OPENCLAW_MEDIA_DIR", raising=False)
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    d = OpenClawImageClient(token="t").media_dir
    assert d == tmp_path / ".openclaw" / "media" / "tool-image-generation"


# --------------------------------------------------------------------------- #
# Provider integration through imagegen: selection, cache hit, raise-on-failure.
# --------------------------------------------------------------------------- #

def test_provider_selected_when_token_present(monkeypatch):
    monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", "openclaw")
    p = imagegen.get_provider()
    assert isinstance(p, imagegen.OpenClawImageProvider)
    assert p.name == "openclaw"


def test_provider_degrades_to_null_without_token(monkeypatch):
    monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", "openclaw")
    monkeypatch.delenv("CLAWDND_OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_GATEWAY_PASSWORD", raising=False)
    # No token -> not configured -> selector falls back to null (never crashes).
    assert isinstance(imagegen.get_provider(), imagegen.NullImageProvider)


def test_provider_satisfies_protocol():
    assert isinstance(imagegen.OpenClawImageProvider(), imagegen.ImageProvider)


def test_provider_descriptor_shape(monkeypatch):
    # Provider.generate returns a JSON-serializable descriptor with the image.
    def fake_generate_image(self, prompt, **kw):  # noqa: ANN001
        return ImageResult(task_id="task_1", path="/m/x.png", mime_type="image/png", data=b"PNG")

    monkeypatch.setattr(OpenClawImageClient, "generate_image", fake_generate_image)
    desc = imagegen.OpenClawImageProvider().generate("portrait", "a paladin", seed=4)
    assert desc["provider"] == "openclaw"
    assert desc["kind"] == "portrait"
    assert desc["placeholder"] is False
    assert desc["task_id"] == "task_1"
    assert desc["path"] == "/m/x.png"
    assert "bytes_b64" in desc  # bytes base64-encoded to stay JSON-serializable.
    json.dumps(desc)  # must round-trip as JSON for the cache.


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return tmp_path


def test_2_cache_hit_invokes_provider_once(monkeypatch, state):
    """Second generate() for the same request is served from cache; client fires once."""
    monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", "openclaw")
    calls = {"n": 0}

    def fake_generate_image(self, prompt, **kw):  # noqa: ANN001
        calls["n"] += 1
        return ImageResult(task_id=f"task_{calls['n']}", path=f"/m/{calls['n']}.png", mime_type="image/png")

    monkeypatch.setattr(OpenClawImageClient, "generate_image", fake_generate_image)

    first = imagegen.generate("scene", "a haunted abbey", seed=2, scope="camp1")
    assert first["cache_hit"] is False
    assert first["provider"] == "openclaw"
    assert first["path"] == "/m/1.png"

    second = imagegen.generate("scene", "a haunted abbey", seed=2, scope="camp1")
    assert second["cache_hit"] is True
    assert second["path"] == "/m/1.png"  # the cached descriptor, unchanged.
    assert calls["n"] == 1, "cache hit must not re-invoke the gateway client"


def test_3_unreachable_propagates_as_runtime_error(monkeypatch, state):
    """imagegen.generate() with a down gateway raises so the caller falls back to null."""
    monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", "openclaw")

    def fake_generate_image(self, prompt, **kw):  # noqa: ANN001
        raise OpenClawGatewayUnreachable("OpenClaw gateway unreachable at .../tools/invoke")

    monkeypatch.setattr(OpenClawImageClient, "generate_image", fake_generate_image)

    with pytest.raises(RuntimeError) as exc:
        imagegen.generate("map", "the sunken city", seed=1, scope="camp2")
    assert "openclaw image provider failed" in str(exc.value)
    # Nothing was cached on failure (the descriptor was never produced).
    key = imagegen.content_hash("map", "the sunken city", seed=1, provider="openclaw")
    assert imagegen.cache_read(key, scope="camp2") is None


def test_provider_generate_wraps_any_openclaw_error(monkeypatch):
    """A plain OpenClawImageError (not unreachable) also re-raises as RuntimeError."""

    def fake_generate_image(self, prompt, **kw):  # noqa: ANN001
        raise OpenClawImageError("gateway image_generate HTTP 500")

    monkeypatch.setattr(OpenClawImageClient, "generate_image", fake_generate_image)
    with pytest.raises(RuntimeError, match="openclaw image provider failed"):
        imagegen.OpenClawImageProvider().generate("scene", "x")
