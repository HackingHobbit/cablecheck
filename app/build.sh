#!/bin/zsh
# Builds Cable Check.app next to this script's parent folder.
set -euo pipefail
here="${0:A:h}"
root="${here:h}"
app="$root/Cable Check.app"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
swiftc -O -parse-as-library "$here/CableCheck.swift" -o "$app/Contents/MacOS/CableCheck"
cp "$root/cablecheck.py" "$app/Contents/Resources/"

cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Cable Check</string>
  <key>CFBundleDisplayName</key><string>Cable Check</string>
  <key>CFBundleIdentifier</key><string>local.cablecheck</string>
  <key>CFBundleExecutable</key><string>CableCheck</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

codesign --force --sign - "$app" >/dev/null 2>&1 || true
echo "Built: $app"
