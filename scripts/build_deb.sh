#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ARCH="${1:-$(dpkg --print-architecture)}"

case "$ARCH" in
    amd64|arm64) ;;
    *)
        echo "Unsupported Debian architecture: $ARCH (use amd64 or arm64)." >&2
        exit 2
        ;;
esac

VERSION="$(sed -nE 's/^[[:space:]]*version[[:space:]]*=[[:space:]]*"([^"]+)".*$/\1/p' "$ROOT/pyproject.toml" | head -n 1)"
if [[ -z "$VERSION" ]]; then
    echo "Could not determine the application version." >&2
    exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb is required to build Debian packages." >&2
    exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
PACKAGE_ROOT="$WORK_DIR/kirho_${VERSION}_${ARCH}"

install -d \
    "$PACKAGE_ROOT/DEBIAN" \
    "$PACKAGE_ROOT/opt/kirho" \
    "$PACKAGE_ROOT/usr/bin" \
    "$PACKAGE_ROOT/usr/share/applications" \
    "$PACKAGE_ROOT/usr/share/doc/kirho"

cp "$ROOT/main.py" "$PACKAGE_ROOT/opt/kirho/"
cp -R "$ROOT/kirho" "$PACKAGE_ROOT/opt/kirho/"
cp -R "$ROOT/i18n" "$PACKAGE_ROOT/opt/kirho/"
cp -R "$ROOT/themes" "$PACKAGE_ROOT/opt/kirho/"
install -m 0644 "$ROOT/LICENSE" "$PACKAGE_ROOT/usr/share/doc/kirho/copyright"

cat > "$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: kirho
Version: $VERSION
Section: science
Priority: optional
Architecture: $ARCH
Maintainer: Pablo Alfaro <pabdan2003@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-pyqt6, python3-numpy, python3-scipy, python3-matplotlib, python3-serial
Description: Kirho electronic circuit simulator
 Analog, digital, and mixed-signal circuit simulation.
EOF

cat > "$PACKAGE_ROOT/usr/bin/kirho" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /opt/kirho/main.py "$@"
EOF
chmod 0755 "$PACKAGE_ROOT/usr/bin/kirho"

cat > "$PACKAGE_ROOT/usr/share/applications/kirho.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Kirho
Comment=Electronic circuit simulator
Exec=kirho
Terminal=false
Categories=Education;Science;Engineering;
EOF

mkdir -p "$ROOT/dist"
OUTPUT="$ROOT/dist/Kirho-${VERSION}-Linux-${ARCH}.deb"
dpkg-deb --build --root-owner-group "$PACKAGE_ROOT" "$OUTPUT" >/dev/null
echo "Built $OUTPUT"
