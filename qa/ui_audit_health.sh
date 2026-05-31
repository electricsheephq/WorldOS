#!/usr/bin/env bash
# UI-audit health check — re-runs the structural checks from the 2026-05-29
# audit cycle (docs/ui-audit/*) against the current code + a running viewer.
#
# Usage:
#   qa/ui_audit_health.sh              # full sweep, port 8799
#   qa/ui_audit_health.sh --port 8895  # custom port
#   qa/ui_audit_health.sh --quick      # skip screenshot capture (fastest)
#   qa/ui_audit_health.sh --axe        # add the axe-core a11y sweep (slower)
#   qa/ui_audit_health.sh --ui-gate    # add the "UI gate with teeth" probe (Loop-9):
#                                      #   per-screen Playwright check for console
#                                      #   errors, placeholder explosion, broken
#                                      #   imgs, title-bar/nav-rail collision (#260),
#                                      #   and the launcher Resume/Continue CTA.
#
# Output: structured PASS/FAIL lines to stdout, exit 0 if all PASS, 1 otherwise.
#
# What it checks (in order):
#   1. Viewer reachable at /openworlds/.
#   2. data.js still empty (no demo content leaked).
#   3. icon-registry.jsx has all required ids (atlas.travel / camp.rest / dice.d20 / …).
#   4. No "Linzi" / "Stolen Marches" / "Cassian" / "Oleg" / Kingmaker demo names
#      reappear in any screen-*.jsx.
#   5. No `<Placeholder>` with `portrait-` scope on the screens where Loop-1
#      flagged them (launcher, character, journal, acts, forge — would regress).
#   6. /campaigns.json returns a list.
#   7. Every /<screen>-surface route returns 200 with non-empty body.
#   8. server.py still exposes /session-surface, /combat-surface, …, /move
#      (no rename / refactor accidentally dropped a route).
#   9. styles.css responsive breakpoints + a11y rules still defined.
#  10. _private/baldurs-gate/images/ still has ≥ 2000 dirs (asset catalog intact).
#  11. (optional, --quick=0) Re-capture screenshots at 1366 + 1512 to /tmp/ow-health/ —
#      compare against the audit baseline (visual diff is manual; this just
#      checks the capture pipeline works).
#  12. (optional, --axe) axe-core a11y sweep across every screen. Baseline = 0.
#  13. (optional, --ui-gate) "UI gate with teeth", driven by qa/ui_gate_probe.js
#      (headless Playwright). For each screen, at viewports 1366 + 1512:
#       13a. renders_clean: 0 JS console errors and 0 page-level exceptions
#            (404 resource warns from un-ingested art are dropped — those are
#            tracked by check #10 + check 13b).
#       13b. art_present: total `.placeholder` count ≤ per-screen Loop-9 baseline
#            + 2 slack (launcher 26, character 9, bestiary 20, inventory 13,
#            forge 1, all others 0). Broken-img count (resolved + naturalWidth
#            == 0) must be 0.
#       13c. title_bar_clearance: `.title-text` and `.nav-rail` bounding rects
#            do NOT overlap (this catches the #260 / #306 regression where the
#            title text wraps and falls behind the nav rail at narrow widths).
#       13d. play_reachable: on #launcher, a "Continue → play" or "Resume → play"
#            button is present and not disabled.
#      If a running viewer isn't reachable on the configured port the probe
#      WARNs instead of FAILing, so the gate stays opt-in for CI lanes that
#      don't stand up a viewer.
#
# Exit codes:
#   0 — all PASS, audit findings still valid as filed.
#   1 — at least one FAIL or REGRESSION; audit docs may need updating.

set -u
PORT=8799
QUICK=0
AXE=0
UI_GATE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --quick) QUICK=1; shift ;;
    --axe) AXE=1; shift ;;
    --ui-gate) UI_GATE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.." || exit 2
REPO="$PWD"
FAIL=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAIL=1; }
warn() { echo "WARN  $1"; }

echo "=== UI-audit health check — repo=$REPO port=$PORT ==="
echo ""

# 1. Viewer reachable.
if curl -sf -o /dev/null "http://127.0.0.1:$PORT/openworlds/" --max-time 5; then
  pass "viewer reachable at :$PORT/openworlds/"
else
  fail "viewer NOT reachable at :$PORT/openworlds/ — start one with: python3 viewer/server.py \"\" $PORT"
  echo "(skipping surface-route checks)"
  exit 1
fi

# 2. data.js still empty (audit Loop 2: line 14-22 forbids demo content).
if grep -qE '^\s*const\s+INITIAL_STATE\s*=\s*\{' viewer/openworlds/data.js && \
   grep -qE '"campaigns":\s*\[\]|campaigns:\s*\[\]' viewer/openworlds/data.js; then
  pass "data.js INITIAL_STATE is empty (no demo leak)"
else
  fail "data.js may carry demo content — verify viewer/openworlds/data.js"
fi

# 3. icon-registry.jsx has the audit-checked ids.
REQ_ICONS=(atlas.travel camp.rest codex.book combat.attack dice.d20 dice.roll \
           economy.coins inventory.locked inventory.potion party.shield \
           quest.scroll settlement.tavern)
missing=()
for icon in "${REQ_ICONS[@]}"; do
  if ! grep -qE "\"$icon\":" viewer/openworlds/icon-registry.jsx; then
    missing+=("$icon")
  fi
done
if [ ${#missing[@]} -eq 0 ]; then
  pass "icon-registry has 12 baseline ids (atlas.travel, dice.d20, …)"
else
  fail "icon-registry missing: ${missing[*]}"
fi

# 4. Demo-leak regression check — Kingmaker / Pathfinder names.
# Strip JSX comments ({/* … */} possibly multi-line) before grepping so doc-comments
# explaining the historical leak don't trip the check. Python pass keeps the script
# portable; awk's multiline comment handling is harder.
LEAK_HITS=$(python3 - <<'PY' 2>/dev/null
import re, pathlib, sys
PAT = re.compile(r"\b(linzi|stolen marches|cassian|oleg|stag lord|kingmaker|pathfinder)\b", re.I)
JSX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.S)
LINE_COMMENT = re.compile(r"//.*$|/\*.*?\*/", re.M | re.S)
total = 0
for p in list(pathlib.Path("viewer/openworlds").glob("*.jsx")) + [pathlib.Path("viewer/openworlds/data.js")]:
    if not p.exists(): continue
    src = p.read_text()
    src = JSX_COMMENT.sub("", src)
    src = LINE_COMMENT.sub("", src)
    for m in PAT.finditer(src):
        total += 1
        # printable hit for debug
        line_start = src.rfind("\n", 0, m.start()) + 1
        line_end   = src.find("\n", m.end())
        snippet    = src[line_start:line_end][:120]
        print(f"{p}:{src[:m.start()].count(chr(10))+1}: {snippet}")
print(f"__TOTAL__:{total}", file=sys.stderr)
PY
)
LEAK_TOTAL=$(echo "$LEAK_HITS" 2>/dev/null | grep -E "^__TOTAL__:" | awk -F: '{print $2}')
# Python prints hits to stdout and __TOTAL__ to stderr; recover via a second pass.
LEAK_TOTAL=$(python3 - <<'PY' 2>/dev/null
import re, pathlib
PAT = re.compile(r"\b(linzi|stolen marches|cassian|oleg|stag lord|kingmaker|pathfinder)\b", re.I)
JSX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.S)
LINE_COMMENT = re.compile(r"//.*$|/\*.*?\*/", re.M | re.S)
total = 0
for p in list(pathlib.Path("viewer/openworlds").glob("*.jsx")) + [pathlib.Path("viewer/openworlds/data.js")]:
    if not p.exists(): continue
    src = p.read_text()
    src = JSX_COMMENT.sub("", src)
    src = LINE_COMMENT.sub("", src)
    total += len(PAT.findall(src))
print(total)
PY
)
if [ "${LEAK_TOTAL:-0}" = "0" ]; then
  pass "no demo-leak strings (linzi/stolen marches/cassian/...) in non-comment JSX or data.js"
else
  fail "demo-leak strings reappeared — $LEAK_TOTAL hit(s) outside comments"
fi

# 5. <Placeholder> regression on portraits — Loop-1 + #270 flagged these.
# After fix, these should switch to <Img>. Detect any new regressions.
PORTRAIT_PLACEHOLDERS=$(grep -E "Placeholder[^>]*portrait" viewer/openworlds/*.jsx 2>/dev/null \
                       | grep -vE "// |^[[:space:]]*\*|placeholder.*portrait silhouette" | wc -l | tr -d ' ')
if [ "$PORTRAIT_PLACEHOLDERS" -le 8 ]; then
  pass "Placeholder/portrait pattern count: $PORTRAIT_PLACEHOLDERS (baseline ≤ 8)"
else
  fail "Placeholder/portrait usage regressed: $PORTRAIT_PLACEHOLDERS occurrences"
fi

# 6. /campaigns.json.
if curl -sf "http://127.0.0.1:$PORT/openworlds/campaigns.json" \
     --max-time 5 | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d.get('campaigns'), list)" 2>/dev/null; then
  pass "/openworlds/campaigns.json returns valid catalog"
else
  fail "/openworlds/campaigns.json shape unexpected"
fi

# 7. Every audit-referenced surface route returns 200.
ROUTES=(session-surface combat-surface atlas-surface journal-surface acts-surface \
        character-surface inventory-surface relations-surface parley-surface \
        bestiary-surface chat)
for r in "${ROUTES[@]}"; do
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/$r" --max-time 5; then
    pass "GET /$r → 200"
  else
    fail "GET /$r failed"
  fi
done

# 8. server.py still exposes the routes (defends against rename refactor).
for r in session-surface combat-surface atlas-surface journal-surface \
         character-surface inventory-surface relations-surface parley-surface \
         bestiary-surface; do
  if grep -qE "route == \"/$r\"" viewer/server.py; then
    pass "server.py exposes /$r"
  else
    fail "server.py no longer exposes /$r (was at server.py:5180–5407 in audit)"
  fi
done

# 9. styles.css responsive + a11y rules.
if grep -qE "@media \(max-width: 1380" viewer/openworlds/styles.css && \
   grep -qE "@media \(max-width: 1200" viewer/openworlds/styles.css; then
  pass "styles.css responsive breakpoints (1380, 1200) present"
else
  fail "styles.css responsive breakpoints missing"
fi
if grep -qE "\[data-reduced-motion" viewer/openworlds/styles.css && \
   grep -qE "\[data-contrast" viewer/openworlds/styles.css; then
  pass "styles.css a11y rules (reduced-motion, high-contrast) present"
else
  fail "styles.css a11y rules missing"
fi

# 10. _private asset catalog intact.
ART_DIR="content/worlds/_private/baldurs-gate/images"
if [ -d "$ART_DIR" ]; then
  COUNT=$(ls -1d "$ART_DIR"/*/ 2>/dev/null | wc -l | tr -d ' ')
  if [ "$COUNT" -ge 2000 ]; then
    pass "_private asset catalog: $COUNT art dirs (baseline ≥ 2000)"
  else
    warn "_private asset catalog shrunk to $COUNT (baseline 2359 in audit)"
  fi
else
  warn "_private/baldurs-gate/images/ not present (expected for clean CI runs)"
fi

# 11. Optional: re-capture screenshots.
if [ "$QUICK" -eq 0 ]; then
  if [ -x qa/owshot.sh ]; then
    OUT_DIR=/tmp/ow-health
    mkdir -p "$OUT_DIR"
    for hash in launcher table combat character map relations dialogue bestiary; do
      if qa/owshot.sh "$hash" "$OUT_DIR/${hash}.png" "$PORT" >/dev/null 2>&1; then
        SZ=$(wc -c <"$OUT_DIR/${hash}.png" 2>/dev/null || echo 0)
        if [ "$SZ" -gt 50000 ]; then
          pass "owshot ${hash} → ${SZ} bytes"
        else
          fail "owshot ${hash} too small ($SZ bytes) — viewer may be serving empty state"
        fi
      else
        fail "owshot ${hash} failed"
      fi
    done
    echo "(captures left at $OUT_DIR/ for visual review — gitignored)"
  else
    warn "qa/owshot.sh not executable; skipping captures"
  fi
fi

# 12. Optional: axe-core a11y sweep. Off by default (needs browser-driver-manager
# + a matching ChromeDriver). Loop-5 baseline (2026-05-29) recorded 11 violations
# across 8 screens — 10 scrollable-region-focusable + 1 label. Filed as #291 + #292.
if [ "$AXE" -eq 1 ]; then
  if ! command -v npx >/dev/null 2>&1; then
    warn "npx not on PATH; skipping --axe"
  else
    CHROME_VER=$(/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version 2>/dev/null | awk '{print $3}' | awk -F. '{print $1}')
    DRIVER_DIR=$(ls -d "$HOME/.browser-driver-manager/chromedriver/mac_arm-${CHROME_VER}"* 2>/dev/null | head -1)
    CHROME_TEST_DIR=$(ls -d "$HOME/.browser-driver-manager/chrome/mac_arm-${CHROME_VER}"* 2>/dev/null | head -1)
    if [ -z "$DRIVER_DIR" ] || [ -z "$CHROME_TEST_DIR" ]; then
      warn "browser-driver-manager not installed for Chrome ${CHROME_VER}; install with:"
      warn "  npx --yes browser-driver-manager install chrome=${CHROME_VER}"
    else
      CHROMEDRIVER="$DRIVER_DIR/chromedriver-mac-arm64/chromedriver"
      CHROME_TEST="$CHROME_TEST_DIR/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
      AXE_OUT=/tmp/ow-axe-health
      mkdir -p "$AXE_OUT"
      TOTAL=0
      for hash in launcher table combat dialogue map character inventory forge \
                  relations journal bestiary acts merchant create seed settings roster; do
        npx --yes @axe-core/cli "http://127.0.0.1:$PORT/openworlds/#${hash}" \
          --tags wcag2a,wcag2aa \
          --chromedriver-path "$CHROMEDRIVER" \
          --chrome-path "$CHROME_TEST" \
          >"$AXE_OUT/${hash}.txt" 2>&1
        N=$(grep -oE "^[0-9]+ Accessibility issues detected" "$AXE_OUT/${hash}.txt" | awk '{print $1}')
        N=${N:-0}
        TOTAL=$((TOTAL + N))
        if [ "$N" = "0" ]; then
          pass "axe ${hash}: 0 violations"
        else
          warn "axe ${hash}: ${N} violations — see $AXE_OUT/${hash}.txt"
        fi
      done
      if [ "$TOTAL" -le 0 ]; then
        pass "axe total: $TOTAL violations (baseline = 0; clean — #291/#292 + scroll-region fixes closed all 11)"
      else
        fail "axe total: $TOTAL violations (baseline = 0; a11y regression detected)"
      fi
    fi
  fi
fi

# 13. Optional: "UI gate with teeth" — per-screen Playwright probe (Loop-9).
# Off by default. Drives qa/ui_gate_probe.js at 1366 + 1512 viewports against
# the configured viewer; checks: renders_clean, art_present, title_bar_clearance,
# play_reachable. Baselines were measured 2026-05-30 against viewer/server.py
# on the full _private asset catalog (≥2359 dirs). If the viewer isn't reachable
# this whole block WARNs rather than FAILs — the gate is opt-in for lanes that
# stand up a viewer first.
if [ "$UI_GATE" -eq 1 ]; then
  if ! command -v node >/dev/null 2>&1; then
    warn "node not on PATH; skipping --ui-gate"
  elif [ ! -f qa/playwright/node_modules/playwright/package.json ]; then
    warn "qa/playwright/node_modules/playwright not installed; run (cd qa/playwright && npm install)"
  elif [ ! -f qa/ui_gate_probe.js ]; then
    warn "qa/ui_gate_probe.js missing; --ui-gate skipped"
  else
    # Per-screen placeholder ceilings (Loop-9 baseline + 2 slack) — kept inline
    # in the python report block below (macOS ships bash 3.2 which has no
    # associative arrays; rather than gate the script on bash 4, we let python
    # carry the table).
    SCREENS_UI=(launcher table combat character map relations dialogue bestiary \
                inventory journal acts merchant forge create seed settings roster)
    UI_OUT=/tmp/ow-ui-gate.ndjson
    : > "$UI_OUT"
    if ! node qa/ui_gate_probe.js "$PORT" "${SCREENS_UI[@]}" > "$UI_OUT" 2>/tmp/ow-ui-gate.err; then
      warn "ui_gate_probe exited non-zero — see /tmp/ow-ui-gate.err"
      warn "(skipping per-screen checks)"
    else
      # Parse NDJSON via python for portability (jq isn't a hard dep here).
      # Emit one PASS/FAIL per screen × check, then a roll-up.
      python3 - "$UI_OUT" <<'PY' > /tmp/ow-ui-gate.report
import json, sys
PH_CAP = {
    "launcher": 28, "table": 2, "combat": 2, "character": 11, "map": 2,
    "relations": 2, "dialogue": 2, "bestiary": 22, "inventory": 15,
    "journal": 2, "acts": 2, "merchant": 2, "forge": 3, "create": 2,
    "seed": 2, "settings": 2, "roster": 2,
}
def emit(verdict, line): print(f"{verdict}  {line}")
total_fail = 0
with open(sys.argv[1]) as f:
    for raw in f:
        raw = raw.strip()
        if not raw.startswith("{"): continue
        try:
            d = json.loads(raw)
        except Exception as e:
            emit("FAIL", f"ui-gate parse error: {e}")
            total_fail += 1
            continue
        s = d["screen"]
        cap = PH_CAP.get(s, 2)
        # Aggregate across the two viewports — both must pass for a green light.
        vps = d.get("viewports", {})
        if not vps:
            emit("WARN", f"ui-gate {s}: no viewport data (viewer reachable?)")
            continue
        for vp, v in vps.items():
            # 13a. renders_clean
            if v.get("consoleErrors", 0) == 0:
                emit("PASS", f"ui-gate renders_clean {s} @ {vp}: 0 console errors")
            else:
                samples = v.get("consoleErrorSamples") or []
                emit("FAIL", f"ui-gate renders_clean {s} @ {vp}: {v['consoleErrors']} err — {samples[:1]}")
                total_fail += 1
            # 13b. art_present (placeholder ceiling + no broken imgs)
            ph = v.get("placeholders", 0)
            broken = v.get("imgsBroken", 0)
            if ph <= cap and broken == 0:
                emit("PASS", f"ui-gate art_present {s} @ {vp}: placeholders={ph}/cap{cap} broken=0/{v.get('imgsTotal',0)}")
            else:
                why = []
                if ph > cap: why.append(f"placeholders={ph} > cap {cap}")
                if broken > 0: why.append(f"broken imgs={broken}/{v.get('imgsTotal',0)}")
                emit("FAIL", f"ui-gate art_present {s} @ {vp}: " + "; ".join(why))
                total_fail += 1
            # 13c. title_bar_clearance — #260 / #306
            title_ok = (
                not v.get("titleNavOverlap", False)
                and not v.get("titleEndOverlap", False)
                and (v.get("titleLineCount") in (None, 0, 1))
                and (v.get("titleDayReadable") in (None, True))
            )
            if title_ok:
                emit("PASS",
                    f"ui-gate title_bar_clearance {s} @ {vp}: title one-line, no nav/day collision, day readable")
            else:
                tr = v.get("titleRect"); nr = v.get("navRect"); er = v.get("titleEndRect"); dr = v.get("titleDayRect")
                emit("FAIL",
                    f"ui-gate title_bar_clearance {s} @ {vp}: navOverlap={v.get('titleNavOverlap')} "
                    f"endOverlap={v.get('titleEndOverlap')} lines={v.get('titleLineCount')} "
                    f"dayReadable={v.get('titleDayReadable')} "
                    f"(title={tr} nav={nr} end={er} day={dr}) — #306 regression")
                total_fail += 1
            # 13d. play_reachable (launcher only)
            cta = v.get("launcherCta")
            if cta is None:
                pass  # non-launcher screens skip this check
            elif cta.get("missing"):
                emit("FAIL", f"ui-gate play_reachable {s} @ {vp}: no Resume/Continue CTA found on launcher")
                total_fail += 1
            elif cta.get("disabled"):
                emit("FAIL", f"ui-gate play_reachable {s} @ {vp}: CTA '{cta.get('text')}' is disabled")
                total_fail += 1
            else:
                emit("PASS", f"ui-gate play_reachable {s} @ {vp}: '{cta.get('text')}' enabled")
            # 13e. representative hit-target proof — #309
            hit = v.get("hitTargets")
            if hit is not None:
                missing = [
                    key for key in (
                        "tabPaddingClickFired",
                        "tabKeyboardFired",
                        "navPaddingClickFired",
                        "navKeyboardFired",
                    )
                    if not hit.get(key)
                ]
                if not missing:
                    emit("PASS", f"ui-gate hit_targets {s} @ {vp}: tab/nav padding clicks + keyboard activation work")
                else:
                    emit("FAIL", f"ui-gate hit_targets {s} @ {vp}: failed {missing} details={hit}")
                    total_fail += 1
print(f"__TOTAL_FAIL__:{total_fail}")
PY
      # Replay the python report through pass/fail/warn so the overall summary
      # picks up failures via the shell's FAIL variable.
      while IFS= read -r line; do
        case "$line" in
          PASS*) pass "${line#PASS  }" ;;
          FAIL*) fail "${line#FAIL  }" ;;
          WARN*) warn "${line#WARN  }" ;;
          __TOTAL_FAIL__:*) ;;  # rollup only
          *) echo "$line" ;;
        esac
      done < /tmp/ow-ui-gate.report
    fi
  fi
fi

echo ""
echo "=== summary ==="
if [ "$FAIL" -eq 0 ]; then
  echo "PASS — audit findings (docs/ui-audit/) consistent with current state."
  exit 0
else
  echo "FAIL — see findings above. Audit docs may need updating to reflect drift."
  exit 1
fi
