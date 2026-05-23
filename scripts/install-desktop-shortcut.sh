#!/usr/bin/env bash
# Install a double-click "ClawDnD Dashboard" shortcut + a clickable link on the
# Desktop, so the owner can open the live play/test dashboard without the terminal.
# Re-run any time (e.g. after moving the repo) to refresh the shortcut.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESK="${HOME}/Desktop"
URL="http://127.0.0.1:8765/dashboard"
CMD="$DESK/ClawDnD Dashboard.command"

# The double-clickable shortcut: runs the repo launcher (starts the viewer + opens
# the browser at /dashboard). REPO is baked in at install time.
cat > "$CMD" <<EOF
#!/usr/bin/env bash
# ClawDnD Dashboard — double-click to open the live play/test view in your browser.
LAUNCH="$REPO/clawdnd-dashboard.command"
if [ ! -x "\$LAUNCH" ]; then
  osascript -e 'display alert "ClawDnD Dashboard" message "Could not find the launcher. Is the drive with the repo mounted ($REPO)?"' 2>/dev/null
  echo "Launcher not found: \$LAUNCH"; read -r -p "Press return to close…" _; exit 1
fi
exec "\$LAUNCH" "\$@"
EOF
chmod +x "$CMD"

# A clickable internet-shortcut "link" (opens the URL once the server is running).
cat > "$DESK/ClawDnD Dashboard.webloc" <<WEBLOC
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>URL</key><string>$URL</string></dict></plist>
WEBLOC

echo "Installed on your Desktop:"
echo "  • 'ClawDnD Dashboard.command'  — double-click: starts the viewer + opens $URL"
echo "  • 'ClawDnD Dashboard.webloc'   — clickable link to $URL (once the server is running)"
echo "(First double-click of the .command: right-click → Open to clear the macOS warning.)"
