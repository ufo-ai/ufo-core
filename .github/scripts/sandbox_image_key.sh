#!/usr/bin/env bash
# Write the sandbox image's Dockerfile to $1 with its base pinned to a digest, and print the cache
# key that names the image built from it. The key hashes that pinned file, which carries every layer
# command, the baked build digest (so editing a COPY'd script under core/src/ufo/sandbox/image, or
# the containment module baked beside them, moves the key), and the resolved digest of the floating
# base tag no source file records. Publisher and
# consumer derive the tag from this one command, so a tag that exists was built from exactly this
# definition on exactly this base. The rendered text is the SDK's, not ours: anything it emits that
# this cannot pin exactly once — no FROM, several, an alias, a base whose digest does not resolve —
# fails here, because the alternative is an image built unpinned under a digest-claiming tag.
set -euo pipefail

dockerfile="$1"
rendered="$dockerfile.rendered"
uv run python sandbox/build_template.py --dockerfile >"$rendered"

mapfile -t from_lines < <(grep -n '^FROM[[:space:]]' "$rendered")
if [ "${#from_lines[@]}" -ne 1 ]; then
  echo "expected exactly one FROM in $rendered, found ${#from_lines[@]}" >&2
  exit 1
fi
number="${from_lines[0]%%:*}"
read -r -a fields <<<"${from_lines[0]#*:}"
if [ "${#fields[@]}" -ne 2 ]; then
  echo "unexpected FROM line in $rendered: ${from_lines[0]#*:}" >&2
  exit 1
fi

base="${fields[1]}"
repository="${base%@*}"
if [[ "${repository##*/}" == *:* ]]; then
  repository="${repository%:*}"
fi
digest="$(docker buildx imagetools inspect "$base" --format '{{.Manifest.Digest}}')"
if [[ "$digest" != sha256:* ]]; then
  echo "unexpected base digest for $base: ${digest:-<empty>}" >&2
  exit 1
fi

awk -v number="$number" -v pinned="FROM $repository@$digest" \
  'NR == number { print pinned; next } { print }' "$rendered" >"$dockerfile"
sha256sum <"$dockerfile" | cut -c1-16
