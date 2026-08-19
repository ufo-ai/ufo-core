#!/bin/sh
# Fetches the pinned pdfium dynamic library into preview/.pdfium (or $1 if given).
# pdfium-render binds it at runtime; tests read UFO_PREVIEW_PDFIUM_LIB pointing at it.
set -eu
PDFIUM_TAG="chromium/8009"
DEST="${1:-$(dirname "$0")/../.pdfium}"
case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) PLATFORM="linux-x64"; LIB="lib/libpdfium.so" ;;
    Linux-aarch64) PLATFORM="linux-arm64"; LIB="lib/libpdfium.so" ;;
    Darwin-arm64) PLATFORM="mac-arm64"; LIB="lib/libpdfium.dylib" ;;
    Darwin-x86_64) PLATFORM="mac-x64"; LIB="lib/libpdfium.dylib" ;;
    *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
mkdir -p "$DEST"
TAG_ENCODED=$(printf '%s' "$PDFIUM_TAG" | sed 's|/|%2F|')
URL="https://github.com/bblanchon/pdfium-binaries/releases/download/${TAG_ENCODED}/pdfium-${PLATFORM}.tgz"
curl -fsSL "$URL" | tar -xz -C "$DEST" "$LIB"
mv "$DEST/$LIB" "$DEST/$(basename "$LIB")"
rmdir "$DEST/lib"
echo "$DEST/$(basename "$LIB")"
