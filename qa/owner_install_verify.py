#!/usr/bin/env python3
"""The two checks owner_install.sh cannot express honestly in bash.

`build-report` refuses a report that is not a stamped SUCCESS: Unity's
BuildMacOSPlayer.StampFailedReport deliberately writes a nonempty `result=Failed`
report beside a possibly-stale .app, so "readable and nonempty" accepts a failed build.

`consumed` proves the installed player actually applied the seeded campaign rather
than answering from a stale scene: the engine must serve adventure_demo_v1, the player
must have applied at least one surface, its plate must match the party's location, and
its camera ortho must equal that location's plates_manifest pin.
"""
from __future__ import annotations  # 3.9 system python runs these

import argparse, json, os, sys
from pathlib import Path

from check_always_included_shaders import REQUIRED_SHADERS

CAMPAIGN = "adventure_demo_v1"
ORTHO_TOL = 0.05


def check_build_report(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    if fields.get("result") != "Succeeded":
        raise ValueError(f"build-report.txt result={fields.get('result') or '<missing>'!s}, not Succeeded")
    shader_line = fields.get("alwaysIncludedShaders", "")
    listed = {name.strip() for name in shader_line.split(",") if name.strip()}
    missing = [name for name in REQUIRED_SHADERS if name not in listed]
    if missing:
        raise ValueError(
            f"alwaysIncludedShaders={shader_line or '<missing>'}; missing {', '.join(missing)}")
    return fields


def plate_ortho(manifest: dict, loc_id: str) -> float | None:
    entry = (manifest.get("plates") or {}).get(loc_id)
    pin = entry.get("cameraPin") if isinstance(entry, dict) else None
    return pin.get("ortho") if isinstance(pin, dict) else None


def check_consumed(surface: dict, debug: dict, manifest: dict) -> str:
    if surface.get("campaign_id") != CAMPAIGN:
        raise ValueError(f"engine serves campaign_id={surface.get('campaign_id')!r}, not {CAMPAIGN!r}")
    loc = (surface.get("location") or {}).get("id") or ""
    if not loc:
        raise ValueError("session surface names no current location")
    check_player_ready(debug)
    cam, pin = debug.get("camOrtho"), plate_ortho(manifest, loc)
    if pin is not None:
        if cam is None:
            raise ValueError("player /debug carries no camOrtho (old build)")
        if abs(float(cam) - float(pin)) > ORTHO_TOL:
            raise ValueError(f"camOrtho {cam} != the {loc!r} manifest pin {pin}")
    return f"CONSUMED: {CAMPAIGN} live at {loc} (surf={debug.get('surf')}, camOrtho={cam}, pin={pin})"


def check_player_ready(debug: dict) -> None:
    if int(debug.get("surf") or 0) <= 0:
        raise ValueError("player applied 0 surfaces — it never reached the engine")
    if debug.get("plateLocMatch") is not True:
        raise ValueError("player plate does not match the engine location")


def record_consumption(path: Path, result: str, elapsed: int, detail: str) -> None:
    data = json.loads(path.read_text())
    data.setdefault("gate_results", {})["consumption"] = {
        "result": result, "elapsed_seconds": elapsed, "detail": detail}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-report").add_argument("path", type=Path)
    sub.add_parser("player-ready").add_argument("path", type=Path)
    con = sub.add_parser("consumed")
    for flag in ("surface", "debug", "manifest"):
        con.add_argument(f"--{flag}", required=True, type=Path)
    led = sub.add_parser("ledger")
    led.add_argument("path", type=Path); led.add_argument("--result", required=True)
    led.add_argument("--elapsed", required=True, type=int); led.add_argument("--detail", required=True)
    args = ap.parse_args()
    try:
        if args.cmd == "build-report":
            print("build identity: " + json.dumps(check_build_report(args.path.read_text()), sort_keys=True))
        elif args.cmd == "player-ready":
            check_player_ready(json.loads(args.path.read_text()))
        elif args.cmd == "consumed":
            print(check_consumed(*(json.loads(p.read_text()) for p in (args.surface, args.debug, args.manifest))))
        else:
            record_consumption(args.path, args.result, args.elapsed, args.detail)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"OWNER INSTALL REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
