#!/bin/bash
# Build TUK.app (+ TUK.dmg) on macOS.
#
# WHY: launched from Terminal, macOS lets Python use the microphone because
# Terminal has a mic usage description. Launched at login by launchd (the wake
# daemon), bare Python has none, so macOS kills it: "Python quit unexpectedly".
# TUK.app gives TUK its own identity + NSMicrophoneUsageDescription, so macOS
# shows a normal "TUK would like to access the microphone" prompt instead.
#
# Usage (run from the project folder, with the venv you run TUK in ACTIVE):
#     source .venv/bin/activate        # or whatever env has requirements.txt
#     bash installer/build_mac_app.sh
# Output: dist/TUK.app  and  dist/TUK.dmg
#
# The app is a thin, signed launcher: it runs THIS project folder with THIS
# Python. Keep the project folder where it is (do not delete/move it).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$(python3 -c 'import sys; print(sys.executable)')"
DIST="$ROOT/dist"
APP="$DIST/TUK.app"
DRY="${TUK_DRY_RUN:-0}"

if [ "$(uname)" != "Darwin" ] && [ "$DRY" != "1" ]; then
  echo "This script builds a macOS app and must run on a Mac." >&2; exit 1
fi
[ -f "$ROOT/main.py" ] || { echo "main.py not found in $ROOT" >&2; exit 1; }
if [ "$DRY" != "1" ]; then
  "$PY" -c 'import PyQt6, sounddevice' 2>/dev/null || \
    { echo "Activate the environment that has requirements.txt installed first." >&2; exit 1; }
fi

echo "Project : $ROOT"
echo "Python  : $PY"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ── launcher ────────────────────────────────────────────────────────────────
# Python runs as a CHILD of the app process so macOS attributes microphone /
# camera / accessibility access to TUK.app. Signals are forwarded so
# `launchctl bootout` and Quit stop it cleanly.
cat > "$APP/Contents/MacOS/TUK" <<LAUNCH
#!/bin/bash
APP_DIR="\$(cd "\$(dirname "\$0")/../.." && pwd)"
export TUK_APP_BUNDLE="\$APP_DIR"
export PYTHONUNBUFFERED=1
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:\$PATH"
LOGDIR="\$HOME/Library/Logs/TUK"; mkdir -p "\$LOGDIR"
exec >>"\$LOGDIR/app.log" 2>&1
cd "$ROOT" || exit 1
"$PY" "$ROOT/main.py" "\$@" &
CHILD=\$!
trap 'kill -TERM \$CHILD 2>/dev/null' TERM INT HUP
wait \$CHILD
STATUS=\$?
# 'wait' returns early when a trapped signal arrives; reap the child properly.
wait \$CHILD 2>/dev/null
exit \$STATUS
LAUNCH
chmod +x "$APP/Contents/MacOS/TUK"

# ── Info.plist ──────────────────────────────────────────────────────────────
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.tuk.assistant.app</string>
  <key>CFBundleName</key><string>TUK</string>
  <key>CFBundleDisplayName</key><string>TUK</string>
  <key>CFBundleExecutable</key><string>TUK</string>
  <key>CFBundleIconFile</key><string>TUK</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>TUK listens for the wake word "TUK" and your voice commands.</string>
  <key>NSCameraUsageDescription</key>
  <string>TUK can look through the camera when you ask it to.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>TUK controls other apps when you ask it to.</string>
  <key>NSDesktopFolderUsageDescription</key>
  <string>TUK works with files you ask it to open.</string>
  <key>NSDocumentsFolderUsageDescription</key>
  <string>TUK works with files you ask it to open.</string>
  <key>NSDownloadsFolderUsageDescription</key>
  <string>TUK works with files you ask it to open.</string>
</dict></plist>
PLIST

# ── icon (best effort; ignored if it fails) ─────────────────────────────────
if command -v sips >/dev/null && command -v iconutil >/dev/null && [ -f "$ROOT/config/jarvis.ico" ]; then
  ( set +e
    TMP="$(mktemp -d)"; SET="$TMP/TUK.iconset"; mkdir -p "$SET"
    sips -s format png "$ROOT/config/jarvis.ico" --out "$TMP/base.png" >/dev/null 2>&1
    for s in 16 32 64 128 256 512; do
      sips -z $s $s "$TMP/base.png" --out "$SET/icon_${s}x${s}.png" >/dev/null 2>&1
      sips -z $((s*2)) $((s*2)) "$TMP/base.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$SET" -o "$APP/Contents/Resources/TUK.icns" >/dev/null 2>&1
    rm -rf "$TMP" ) || true
fi

# ── sign (ad-hoc): gives macOS a stable identity to attach permissions to ───
if command -v codesign >/dev/null; then
  codesign --force --deep --sign - "$APP"
  codesign --verify --deep "$APP" && echo "Signed (ad-hoc)."
fi

# ── dmg ─────────────────────────────────────────────────────────────────────
if command -v hdiutil >/dev/null; then
  STAGE="$(mktemp -d)"
  cp -R "$APP" "$STAGE/TUK.app"
  ln -s /Applications "$STAGE/Applications"
  rm -f "$DIST/TUK.dmg"
  hdiutil create -volname "TUK" -srcfolder "$STAGE" -ov -format UDZO "$DIST/TUK.dmg" >/dev/null
  rm -rf "$STAGE"
  echo "Built: $DIST/TUK.dmg"
fi
echo "Built: $APP"
cat <<'NEXT'

Next:
  1. Quit TUK, then drag dist/TUK.app to /Applications (or use TUK.dmg).
  2. First run:  open /Applications/TUK.app   -> allow the Microphone prompt.
  3. In TUK turn ALWAYS-ON off, then on again (re-registers the login agent
     so it starts through TUK.app instead of bare Python).
NEXT
