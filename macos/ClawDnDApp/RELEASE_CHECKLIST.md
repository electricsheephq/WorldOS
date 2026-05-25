# ClawDnD Native macOS Release Checklist

The v0.3 macOS lane starts with a locally signed development app. Notarization is
release-trust work after the local shell, provider bridge, and dashboard hosting
are stable.

## Local build

```bash
./script/build_and_run.sh --verify
```

## Signing state

```bash
security find-identity -p codesigning -v
codesign --verify --deep --strict dist/ClawDnD.app
spctl -a -vv dist/ClawDnD.app
```

The build script ad-hoc signs the local app bundle when `codesign` is available.
Gatekeeper assessment may still reject the app until a Developer ID certificate,
hardened runtime, and notarization flow are configured.

## Distribution blockers to track separately

- Developer ID signing identity.
- Hardened runtime entitlements.
- Notarization profile and CI secret handling.
- User-facing update channel.
- Copyright/private world seed exclusion from packaged artifacts.
