# #1456 evidence — ScreenCaptureKit no-activation capture

`sck_textedit_nonfrontmost.png` was produced by the new `capture` subcommand of
`qa/native_palette/native_input.swift` (ScreenCaptureKit / `SCScreenshotManager`), the #1456
no-activation capture path that replaces the old activate-before-capture behavior.

Repro (run on macOS 14+ with Screen Recording granted to the terminal):

```
swiftc qa/native_palette/native_input.swift -o /tmp/native_input
printf 'hello\n' > /tmp/doc.txt
open -g -a TextEdit /tmp/doc.txt          # -g == open in BACKGROUND, never activated
/tmp/native_input capture TextEdit qa/evidence/1456/sck_textedit_nonfrontmost.png
osascript -e 'quit app "TextEdit"'
```

Observed during capture:

- frontmost app before AND after capture: **Discord** (TextEdit was NEVER frontmost / never activated)
- helper result: `{"ok":true,"owner":"TextEdit","window_id":20100,"x":213,"y":78,"w":673,"h":439,"px_w":1346,"px_h":878,"scale":2,"on_screen":true}`
- output: a real 1346x878 PNG showing the background TextEdit window's content (not black)

This demonstrates the property #1456 requires: the harness images a target window **without
stealing focus or switching Spaces**. The screencapture path picking up focus was the owner-report
root cause.

Follow-up (deferred, needs an idle machine): the full WorldOSPlayer loop — windowed launch +
`qa/player_smoke.sh` end-to-end (real player window captured via SCK, scripted move/attack landing)
— was NOT run here to honor the no-hijack constraint (the owner-active guard defers it while the Mac
is in use). Run `FORCE_PLAYER_QA=1 qa/player_smoke.sh` (or leave the guard to auto-run) when the box
is idle to validate the player-side capture calibration.
