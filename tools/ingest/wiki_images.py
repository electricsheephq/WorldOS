#!/usr/bin/env python3
"""Stage 1 of the wiki IMAGE-ingest pipeline: fetch lead images → servable descriptors.

OFFLINE content tooling (NOT an MCP tool, NOT imported by the engine). Stdlib only
(urllib + json), so it adds no dependency and can run anywhere.

Given a manifest of {title, scope, kind(portrait|item|scene|map)} entries it:
  1. Fetches the page's lead image URL via the MediaWiki API
     (action=query&prop=pageimages&piprop=original, or imageinfo&iiprop=url).
  2. Downloads the image bytes.
  3. Writes a descriptor JSON to the world's gitignored _private images dir so the
     viewer's /image endpoint can serve it.

## Licensing / storage discipline (LOAD-BEARING)

Official game / wiki images (BG3 portraits, Larian/WotC item icons) are © — they are
written ONLY to the gitignored path:

    content/worlds/_private/<world_id>/images/<scope>/

…which is covered by the /content/worlds/_private/ rule in .gitignore. They are NEVER
committed. CC-BY-SA wiki images may be kept WITH per-file attribution.

Each image gets a sidecar `<filename>.provenance.json` carrying:
  - source_url  — the wiki image file URL (canon or thumbnail)
  - page_url    — the wiki article URL the image came from
  - license     — license string from the manifest source
  - attribution — attribution string from the manifest source
  - fetched_at  — unix timestamp

## Manifest format

```json
{
  "world_id": "baldurs-gate",
  "rate_delay_seconds": 0.5,
  "sources": [
    {
      "wiki": "bg3.wiki",
      "script_path": "/w",
      "license": "CC BY-SA 4.0 / CC BY-NC-SA 4.0 (dual, non-commercial fan use)",
      "attribution": "Image from bg3.wiki; dual-licensed CC BY-SA 4.0 / CC BY-NC-SA 4.0.",
      "images": [
        {"title": "Shadowheart", "scope": "portrait:shadowheart", "kind": "portrait"},
        {"title": "Elfsong Tavern", "scope": "scene:elfsong-tavern", "kind": "scene"}
      ]
    }
  ]
}
```

The `scope` field maps directly to the `/image?scope=` viewer parameter. The descriptor
written is exactly what viewer/server.py `_serve_image` / `_latest_descriptor` expects:
  {"path": "<abs>", "mime_type": "image/jpeg", "scope": "...", ...}

## Usage

    python3 tools/ingest/wiki_images.py [manifest_images.json] [--max N] [--dry-run]

Resumable + idempotent (skips already-written scopes), polite (rate-limited + User-Agent).
A `--dry-run` flag logs what would be fetched without downloading or writing anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_UA = "ClawDnD-image-ingest/0.1 (private, non-commercial fan project; MediaWiki API)"

# Descriptor filename written under the scope dir (single file per scope —
# the viewer's _latest_descriptor picks newest *.json so this is stable).
_DESCRIPTOR_FILENAME = "wiki_ingest.json"


# --------------------------------------------------------------------------- #
# MediaWiki API helpers
# --------------------------------------------------------------------------- #

def _api(wiki: str, params: dict, delay: float, script_path: str = "") -> dict:
    """One polite GET against the wiki's api.php. Returns parsed JSON (or {} on error)."""
    sp = ("/" + script_path.strip("/")) if script_path.strip("/") else ""
    qs = urllib.parse.urlencode({**params, "format": "json", "maxlag": "5"})
    url = f"https://{wiki}{sp}/api.php?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(delay)
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"  ! api error ({attempt + 1}/3): {e}", file=sys.stderr)
            time.sleep(1.5 * (attempt + 1))
    return {}


def _fetch_lead_image_url(
    wiki: str,
    title: str,
    delay: float,
    script_path: str = "",
) -> str | None:
    """Fetch the lead/thumbnail image URL for a wiki page.

    Strategy A — pageimages with piprop=original: fastest, returns the full-res
    page-image (the infobox portrait for character pages). Falls through to strategy
    B when not available (e.g. the page has no infobox image or the wiki doesn't
    support piprop=original).

    Strategy B — query imageinfo on the first image listed in the page (prop=images).
    Slower (two round-trips) but works for any page with any image.

    Returns an absolute image URL or None on failure.
    """
    # Strategy A: pageimages
    data = _api(wiki, {
        "action": "query",
        "titles": title,
        "prop": "pageimages",
        "piprop": "original",
        "redirects": "1",
    }, delay, script_path)
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        original = page.get("original", {})
        url = original.get("source", "")
        if url:
            return url

    # Strategy B: first image listed on the page → imageinfo
    data2 = _api(wiki, {
        "action": "query",
        "titles": title,
        "prop": "images",
        "imlimit": "5",
        "redirects": "1",
    }, delay, script_path)
    pages2 = data2.get("query", {}).get("pages", {})
    img_titles: list[str] = []
    for page in pages2.values():
        for img in page.get("images", []):
            t = img.get("title", "")
            if t:
                img_titles.append(t)
    if not img_titles:
        return None
    # Fetch image info for the first candidate
    for img_title in img_titles[:3]:
        data3 = _api(wiki, {
            "action": "query",
            "titles": img_title,
            "prop": "imageinfo",
            "iiprop": "url",
            "redirects": "1",
        }, delay, script_path)
        pages3 = data3.get("query", {}).get("pages", {})
        for page in pages3.values():
            for ii in page.get("imageinfo", []):
                url = ii.get("url", "")
                if url:
                    return url
    return None


def _download_image(url: str) -> tuple[bytes, str] | None:
    """Download image bytes + MIME type from `url`. Returns (bytes, mime) or None."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                ctype = r.headers.get_content_type() or ""
                return data, ctype
        except Exception as e:  # noqa: BLE001
            print(f"  ! download error ({attempt + 1}/3): {e}", file=sys.stderr)
            time.sleep(1.5 * (attempt + 1))
    return None


# --------------------------------------------------------------------------- #
# Output path helpers
# --------------------------------------------------------------------------- #

def _safe_scope(scope: str | None) -> str:
    """Reduce an arbitrary scope id to a single safe path segment (alnum, -, _).

    Mirrors imagegen._safe_scope and viewer/server.py's _safe_scope: only alnum,
    hyphen, and underscore survive; all other characters (including ':', '.', '/',
    '\\') become underscores. Length-capped at 128. This is the path-traversal
    guard — a scope like '../../etc/passwd' becomes '______etc_passwd'.
    """
    if not scope:
        return ""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(scope))[:128]


def _private_images_dir(world_id: str, scope: str) -> Path:
    """Gitignored output root for one scope: content/worlds/_private/<world_id>/images/<scope>/.

    The scope is sanitised via _safe_scope so a manifest entry like
    'portrait:shadowheart' becomes 'portrait_shadowheart' (and path-traversal
    characters can't escape the _private root). The containment check in
    write_descriptor() is a belt-and-suspenders guard.
    """
    return _REPO_ROOT / "content" / "worlds" / "_private" / world_id / "images" / _safe_scope(scope)


# Known image MIME types that mimetypes.guess_extension may not know on all platforms.
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _ext_for_mime(mime: str, url: str) -> str:
    """Best file extension: prefer explicit table, then Content-Type, fall back to URL path."""
    bare = mime.split(";")[0].strip().lower()
    # 1. Explicit table (cross-platform reliable).
    if bare in _MIME_TO_EXT:
        return _MIME_TO_EXT[bare]
    # 2. stdlib mimetypes (may not know webp/avif on older systems).
    #    Skip generic fallbacks like ".bin" that don't describe the actual format.
    ext = mimetypes.guess_extension(bare, strict=False) or ""
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"
    if ext and ext not in (".bin", ".exe"):
        return ext
    # 3. URL path extension — more reliable for image/* when MIME is generic.
    url_path = urllib.parse.urlparse(url).path
    ext = Path(url_path).suffix.lower()
    return ext or ".bin"


def write_descriptor(
    world_id: str,
    scope: str,
    image_data: bytes,
    mime: str,
    *,
    source_url: str,
    page_url: str,
    license: str,
    attribution: str,
) -> Path:
    """Write image + sidecar provenance + descriptor to the _private images dir.

    Containment guarantee: the output dir must resolve under
    content/worlds/_private/<world_id>/images/ — path-traversal safe.
    """
    out_dir = _private_images_dir(world_id, scope)
    # Containment check: the resolved dir must be under the _private images root.
    expected_root = (_REPO_ROOT / "content" / "worlds" / "_private" / world_id / "images").resolve()
    try:
        if expected_root not in out_dir.resolve().parents and out_dir.resolve() != expected_root:
            raise ValueError(f"scope resolved outside _private images root: {out_dir}")
    except OSError as exc:
        raise ValueError(f"bad output path: {out_dir}") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _ext_for_mime(mime, source_url)
    img_path = out_dir / f"image{ext}"
    img_path.write_bytes(image_data)

    # Provenance sidecar (per-file attribution, as promised).
    prov = {
        "source_url": source_url,
        "page_url": page_url,
        "license": license,
        "attribution": attribution,
        "mime_type": mime,
        "fetched_at": time.time(),
    }
    prov_path = out_dir / f"image{ext}.provenance.json"
    prov_path.write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")

    # Viewer descriptor (what _latest_descriptor / _serve_image reads).
    desc = {
        "scope": scope,
        "path": str(img_path.resolve()),
        "mime_type": mime,
        "source_url": source_url,
        "license": license,
        "attribution": attribution,
        "ingested_at": time.time(),
    }
    desc_path = out_dir / _DESCRIPTOR_FILENAME
    desc_path.write_text(json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8")
    return desc_path


# --------------------------------------------------------------------------- #
# Pipeline entry point
# --------------------------------------------------------------------------- #

def ingest_manifest(manifest_path: Path, *, max_override: int | None, dry_run: bool) -> int:
    """Run the image-ingest pipeline from a manifest file. Returns exit code."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    world_id: str = manifest["world_id"]
    delay = float(manifest.get("rate_delay_seconds", 0.5))
    sources: list[dict] = manifest.get("sources", [])

    fetched = skipped = missing = errors = 0
    for src in sources:
        wiki: str = src["wiki"]
        script_path: str = src.get("script_path", "")
        license_str: str = src.get("license", "")
        attribution_str: str = src.get("attribution", "")
        images: list[dict] = src.get("images", [])
        if max_override is not None:
            images = images[:max_override]

        print(f"[image-ingest] wiki={wiki} world={world_id} images={len(images)}")

        for entry in images:
            title: str = entry["title"]
            scope: str = entry["scope"]
            kind: str = entry.get("kind", "scene")

            out_dir = _private_images_dir(world_id, scope)
            desc_path = out_dir / _DESCRIPTOR_FILENAME

            if desc_path.exists() and not dry_run:
                skipped += 1
                print(f"  [skip] {scope} (already ingested)")
                continue

            # Derive page URL for provenance.
            sp = ("/" + script_path.strip("/")) if script_path.strip("/") else ""
            page_url = f"https://{wiki}{sp}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"

            print(f"  [fetch] {title!r} → scope={scope}")

            if dry_run:
                print(f"    dry-run: would fetch lead image from {wiki}")
                continue

            img_url = _fetch_lead_image_url(wiki, title, delay, script_path)
            if not img_url:
                print(f"  ! no lead image found for {title!r} on {wiki}", file=sys.stderr)
                missing += 1
                continue

            print(f"    image url: {img_url}")
            result = _download_image(img_url)
            if result is None:
                print(f"  ! download failed for {img_url}", file=sys.stderr)
                errors += 1
                continue

            image_bytes, mime = result
            if not mime or mime == "application/octet-stream":
                mime = mimetypes.guess_type(img_url)[0] or "image/jpeg"

            try:
                write_descriptor(
                    world_id,
                    scope,
                    image_bytes,
                    mime,
                    source_url=img_url,
                    page_url=page_url,
                    license=license_str,
                    attribution=attribution_str,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  ! write failed for {scope}: {exc}", file=sys.stderr)
                errors += 1
                continue

            fetched += 1
            print(f"    ok ({len(image_bytes)} bytes, {mime})")

    print(
        f"[image-ingest] done: {fetched} fetched, {skipped} cached-skip, "
        f"{missing} missing, {errors} errors"
    )
    return 1 if errors else 0


def main() -> int:
    default_manifest = _HERE / "manifest_images.json"
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", nargs="?", default=str(default_manifest),
                    help="path to the image manifest JSON (default: manifest_images.json)")
    ap.add_argument("--max", type=int, default=None,
                    help="cap the number of images per source (for dry-runs / testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be fetched without downloading or writing")
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    return ingest_manifest(manifest_path, max_override=args.max, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
