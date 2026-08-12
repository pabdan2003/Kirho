#!/usr/bin/env sh
# Build a distributable macOS application and drag-and-drop installer image.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)
DMG="dist/Kirho-${VERSION}-macOS.dmg"

python3 -m PyInstaller \
  --noconfirm \
  --windowed \
  --name Kirho \
  --osx-bundle-identifier com.github.pabdan2003.kirho \
  --icon assets/kirho.icns \
  --add-data "i18n:i18n" \
  main.py

/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" \
  "dist/Kirho.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" \
  "dist/Kirho.app/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" \
    "dist/Kirho.app/Contents/Info.plist"
codesign --force --deep --sign - "dist/Kirho.app"

rm -f "$DMG"
hdiutil create \
  -volname "Kirho" \
  -srcfolder "dist/Kirho.app" \
  -ov \
  -format UDZO \
  "$DMG"
