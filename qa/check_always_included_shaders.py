#!/usr/bin/env python3
"""check_always_included_shaders.py — box-build PRE-FLIGHT gate for #1674.

Ground truth checked before writing this (2026-07-22): the Unity project's ProjectSettings/
GraphicsSettings.asset is NOT carried in this repo (the box project holds it), so a Python gate cannot
inspect the Always-Included Shaders list of a not-yet-built .app directly. What the repo DOES own is the
shipping build source — extensions/renderers/unity/scripts/BuildMacOSPlayer.cs — which (as of #1674) bakes
the required Shader.Find-resolved shaders into Always-Included at build time via EnsureAlwaysIncludedShaders().

The regression #1674 caught: WorldOS/ActorSilhouette (the #1545/#1651 walk-behind silhouette) was resolved
at runtime via Shader.Find and referenced by no asset, so the player build stripped it; the shipped .app
logged "WorldOS/ActorSilhouette not found (add to Always-Included Shaders); walk-behind mask disabled" and
the feature silently no-op'd. Only the manual Tools/WorldOS/W5b menu item added it — a step a box rebuild
could (and did) skip. This gate makes that class un-shippable by FAILING the pre-flight whenever the build
SOURCE stops guaranteeing a required shader is in Always-Included (someone deletes the registration, drops a
shader from the list, or the shader file goes missing).

What it verifies (all machine-checkable against repo files):
  1. Each required shader FILE exists under extensions/renderers/unity/shaders/.
  2. BuildMacOSPlayer.cs calls EnsureAlwaysIncludedShaders() from its player Build() path.
  3. BuildMacOSPlayer.cs's RequiredAlwaysIncluded list names every required shader.

Optionally (when --build-report PATH is given, i.e. post-build on the box) it also asserts the produced
build-report.txt's `alwaysIncludedShaders=` line lists every required shader — the belt-and-braces
post-build confirmation the runbook's packaged-check step runs against the installed app's report.

Exit codes (tri-state, matching the sibling qa gates): 0 clean, 1 findings, 2 harness error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Keep this list in sync with BuildMacOSPlayer.RequiredAlwaysIncluded (the C# source of truth).
REQUIRED_SHADERS = ["WorldOS/OccluderDepth", "WorldOS/ActorSilhouette"]

BUILD_SCRIPT_REL = "extensions/renderers/unity/scripts/BuildMacOSPlayer.cs"
SHADER_DIR_REL = "extensions/renderers/unity/shaders"
# shader-name -> its .shader filename (the file whose first line declares `Shader "<name>"`).
SHADER_FILES = {
    "WorldOS/OccluderDepth": "OccluderDepth.shader",
    "WorldOS/ActorSilhouette": "ActorSilhouette.shader",
}


def evaluate_build_source(build_script_text: str, required=REQUIRED_SHADERS) -> list[str]:
    """Pure check of the build-source guarantee. Returns a list of problem strings ([] == clean).

    Testable with no filesystem: pass the BuildMacOSPlayer.cs text and the required shader names.
    """
    problems: list[str] = []
    # Require the INVOCATION (`...();`), not merely the method definition (`...() {`) — otherwise deleting
    # the call while leaving the helper defined would slip past the gate.
    if "EnsureAlwaysIncludedShaders();" not in build_script_text:
        problems.append(
            "BuildMacOSPlayer.cs no longer calls EnsureAlwaysIncludedShaders() — the player build "
            "does not guarantee runtime shaders survive variant stripping (#1674 regression class)."
        )
    for name in required:
        if name not in build_script_text:
            problems.append(
                f"required Always-Included shader not registered in the build source: {name} "
                "(add it to BuildMacOSPlayer.RequiredAlwaysIncluded)."
            )
    return problems


def evaluate_build_report(report_text: str, required=REQUIRED_SHADERS) -> list[str]:
    """Post-build confirmation: the report's `alwaysIncludedShaders=` line lists every required shader."""
    problems: list[str] = []
    line = None
    for ln in report_text.splitlines():
        if ln.startswith("alwaysIncludedShaders="):
            line = ln[len("alwaysIncludedShaders="):]
            break
    if line is None:
        problems.append("build-report.txt has no alwaysIncludedShaders= line (stale/pre-#1674 build).")
        return problems
    listed = {s.strip() for s in line.split(",") if s.strip()}
    for name in required:
        if name not in listed:
            problems.append(f"build-report alwaysIncludedShaders is missing: {name} (shader stripped from the built .app).")
    return problems


def run(root: Path = ROOT, build_report: Path | None = None) -> tuple[int, list[str]]:
    """Filesystem wrapper. Returns (exit_code, messages)."""
    problems: list[str] = []

    # 1) shader files present
    shader_dir = root / SHADER_DIR_REL
    for name in REQUIRED_SHADERS:
        fname = SHADER_FILES.get(name)
        if not fname:
            problems.append(f"no known .shader filename mapped for required shader {name} (update SHADER_FILES).")
            continue
        if not (shader_dir / fname).is_file():
            problems.append(f"required shader file missing: {SHADER_DIR_REL}/{fname} (for {name}).")

    # 2 + 3) build-source guarantee
    build_script = root / BUILD_SCRIPT_REL
    try:
        text = build_script.read_text(encoding="utf-8")
    except OSError as exc:
        return 2, [f"harness error: cannot read {BUILD_SCRIPT_REL}: {exc}"]
    problems.extend(evaluate_build_source(text))

    # optional post-build confirmation
    if build_report is not None:
        try:
            rtext = Path(build_report).read_text(encoding="utf-8")
        except OSError as exc:
            return 2, [f"harness error: cannot read build report {build_report}: {exc}"]
        problems.extend(evaluate_build_report(rtext))

    return (1 if problems else 0), problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pre-flight gate: required shaders are baked into Always-Included (#1674).")
    ap.add_argument("--root", type=Path, default=ROOT, help="repo root (default: inferred from this file).")
    ap.add_argument("--build-report", type=Path, default=None,
                    help="optional BuildOutput/build-report.txt to also confirm post-build (box packaged-check).")
    args = ap.parse_args(argv)

    code, messages = run(args.root, args.build_report)
    if code == 0:
        print("[check_always_included_shaders] OK — build guarantees " + ", ".join(REQUIRED_SHADERS) + " in Always-Included.")
    else:
        tag = "HARNESS ERROR" if code == 2 else "FAIL"
        print(f"[check_always_included_shaders] {tag} (#1674):")
        for m in messages:
            print("  - " + m)
    return code


if __name__ == "__main__":
    sys.exit(main())
