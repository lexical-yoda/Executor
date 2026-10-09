#!/bin/sh
# Fonts and icons the map needs, from Protomaps' basemaps-assets at a pinned
# commit (fonts: SIL Open Font License; see fonts/OFL.txt). They are served by
# Executor itself, so the page never loads anything from another origin.
set -eu
COMMIT=028c18f713baecad011301ff7a69acc39bcc2ae7
DEST="$(cd "$(dirname "$0")/.." && pwd)/public/map"
if [ -f "$DEST/.commit" ] && [ "$(cat "$DEST/.commit")" = "$COMMIT" ]; then
  exit 0
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://codeload.github.com/protomaps/basemaps-assets/tar.gz/$COMMIT"
if command -v curl >/dev/null 2>&1; then curl -fsSL -o "$TMP/assets.tgz" "$URL"; else wget -qO "$TMP/assets.tgz" "$URL"; fi
tar -xzf "$TMP/assets.tgz" -C "$TMP"
SRC="$TMP/basemaps-assets-$COMMIT"
rm -rf "$DEST"
mkdir -p "$DEST/fonts" "$DEST/sprites"
for font in "Noto Sans Regular" "Noto Sans Medium" "Noto Sans Italic"; do
  cp -R "$SRC/fonts/$font" "$DEST/fonts/"
done
cp "$SRC/fonts/OFL.txt" "$DEST/fonts/"
cp "$SRC"/sprites/v4/dark.json "$SRC"/sprites/v4/dark.png "$SRC"/sprites/v4/dark@2x.json "$SRC"/sprites/v4/dark@2x.png "$DEST/sprites/"
echo "$COMMIT" > "$DEST/.commit"
echo "map assets ready in $DEST"
