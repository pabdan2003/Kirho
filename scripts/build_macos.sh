#!/usr/bin/env sh
# Build a distributable macOS application and drag-and-drop installer image.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)
DMG="dist/Kirho-${VERSION}-macOS.dmg"
DMG_RW="dist/.Kirho-${VERSION}-macOS-rw.dmg"

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name Kirho \
  --osx-bundle-identifier com.github.pabdan2003.kirho \
  --icon assets/kirho.icns \
  --add-data "i18n:i18n" \
  main.py

PLIST="dist/Kirho.app/Contents/Info.plist"

/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" \
  "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" \
  "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" \
    "$PLIST"

# Register .csin so Finder can use Kirho's document icon and open the file.
cp assets/kirho.icns "dist/Kirho.app/Contents/Resources/kirho.icns"
plist_add() {
  /usr/libexec/PlistBuddy -c "$1" "$PLIST" 2>/dev/null || true
}
plist_add "Add :UTExportedTypeDeclarations array"
plist_add "Add :UTExportedTypeDeclarations:0 dict"
plist_add "Add :UTExportedTypeDeclarations:0:UTTypeIdentifier string com.github.pabdan2003.kirho.csin"
plist_add "Add :UTExportedTypeDeclarations:0:UTTypeDescription string Kirho Circuit"
plist_add "Add :UTExportedTypeDeclarations:0:UTTypeConformsTo array"
plist_add "Add :UTExportedTypeDeclarations:0:UTTypeConformsTo:0 string public.data"
plist_add "Add :UTExportedTypeDeclarations:0:UTTypeTagSpecification dict"
plist_add "Add :UTExportedTypeDeclarations:0:UTTypeTagSpecification:public.filename-extension array"
plist_add "Add :UTExportedTypeDeclarations:0:UTTypeTagSpecification:public.filename-extension:0 string csin"
plist_add "Add :UTExportedTypeDeclarations:0:UTTypeTagSpecification:public.mime-type string application/x-kirho-circuit"
plist_add "Add :CFBundleDocumentTypes array"
plist_add "Add :CFBundleDocumentTypes:0 dict"
plist_add "Add :CFBundleDocumentTypes:0:CFBundleTypeName string Kirho Circuit"
plist_add "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Editor"
plist_add "Add :CFBundleDocumentTypes:0:LSHandlerRank string Owner"
plist_add "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array"
plist_add "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string csin"
plist_add "Add :CFBundleDocumentTypes:0:LSItemContentTypes array"
plist_add "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string com.github.pabdan2003.kirho.csin"
plist_add "Add :CFBundleDocumentTypes:0:CFBundleTypeIconFile string kirho.icns"
codesign --force --deep --sign - "dist/Kirho.app"

rm -f "$DMG" "$DMG_RW"
DMG_SIZE_KB=$(du -sk "dist/Kirho.app" | awk '{print $1 + 12288}')
hdiutil create -size "${DMG_SIZE_KB}k" -fs HFS+ -volname "Kirho" -ov "$DMG_RW"

MOUNT_DIR=
ICON_TMP=
cleanup() {
  [ -z "$MOUNT_DIR" ] || hdiutil detach "$MOUNT_DIR" -quiet || true
  [ -z "$ICON_TMP" ] || rm -rf "$ICON_TMP"
  rm -f "$DMG_RW"
}
trap cleanup EXIT HUP INT TERM

MOUNT_DIR=$(hdiutil attach -readwrite -noverify -noautoopen "$DMG_RW" | sed -n 's/.*\t\(\/Volumes\/.*\)$/\1/p')
ditto "dist/Kirho.app" "$MOUNT_DIR/Kirho.app"
ln -s /Applications "$MOUNT_DIR/Applications"
mkdir "$MOUNT_DIR/.background"
cp assets/dmg-background.png "$MOUNT_DIR/.background/background.png"
cp assets/kirho.icns "$MOUNT_DIR/.VolumeIcon.icns"
SetFile -a V "$MOUNT_DIR/.VolumeIcon.icns"
SetFile -a C "$MOUNT_DIR"

osascript - "$MOUNT_DIR" <<'APPLESCRIPT'
on run argv
set volumeName to do shell script "/usr/bin/basename " & quoted form of (item 1 of argv)
tell application "Finder"
  tell disk volumeName
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set bounds of container window to {100, 100, 740, 500}
    set viewOptions to the icon view options of container window
    set arrangement of viewOptions to not arranged
    set icon size of viewOptions to 128
    set background picture of viewOptions to file ".background:background.png"
    set position of item "Kirho.app" of container window to {180, 210}
    set position of item "Applications" of container window to {470, 210}
    close
  end tell
end tell
end run
APPLESCRIPT

sync
hdiutil detach "$MOUNT_DIR"
MOUNT_DIR=
hdiutil convert "$DMG_RW" -format UDZO -imagekey zlib-level=9 -ov -o "$DMG"

ICON_TMP=$(mktemp -d)
cp assets/kirho.icns "$ICON_TMP/kirho.icns"
sips -i "$ICON_TMP/kirho.icns" >/dev/null
DeRez -only icns "$ICON_TMP/kirho.icns" > "$ICON_TMP/kirho.rsrc"
Rez -append "$ICON_TMP/kirho.rsrc" -o "$DMG"
SetFile -a C "$DMG"
