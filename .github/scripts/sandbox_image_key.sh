#!/usr/bin/env bash
# The key hashes the pinned Dockerfile, the baked build digest, and the resolved digest of the
# floating base tag no source file records. Anything it cannot pin exactly once fails here.
set -euo pipefail

dockerfile="$1"
rendered="$dockerfile.rendered"
uv run python sandbox/build_template.py --dockerfile >"$rendered"

from_lines=()
while IFS= read -r from_line; do
  from_lines+=("$from_line")
done < <(grep -n '^FROM[[:space:]]' "$rendered")
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
shasum -a 256 <"$dockerfile" | cut -c1-16
