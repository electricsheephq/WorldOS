# UI-audit screenshots — local-only

This directory is **gitignored** and intentionally empty in the public repo.

## Why
The audit screenshots reproduce rendered output from `content/worlds/_private/`
— official BG3 portraits (Jaheira, Astarion, …), the Sword Coast wiki map, BG
city scene art, etc. Per `docs/OPENWORLDS_DESIGN_ASSET_POLICY.md` and `.gitignore`,
those source images are © (Wizards Fan Content Policy / wiki licenses) and never
commit; the same logic applies to derivative screenshots that embed them.

## How to regenerate locally

```sh
# From the repo root.
# Viewer must be running (default port 8799):
CLAWDND_STATE_DIR="$(mktemp -d)" CLAWDND_REPO_ROOT="$PWD" \
  python3 viewer/server.py "" 8799 &

# Then capture all 16 screens:
for s in launcher table combat dialogue map character inventory forge \
         relations journal bestiary acts merchant create seed settings; do
  qa/owshot.sh "$s" "docs/ui-audit/screenshots/${s}-1512.png" 8799
done
```

The capture script uses headless Chrome at 1512×982 with a fresh profile per port
(no cache). See `qa/owshot.sh` for the exact invocation.

## Filenames the audit docs reference
The per-screen audit files at `docs/ui-audit/screens/<screen>.md` reference
`docs/ui-audit/screenshots/<screen>-1512.png`. After running the regeneration
loop above the links resolve locally; on GitHub they stay broken by design.
