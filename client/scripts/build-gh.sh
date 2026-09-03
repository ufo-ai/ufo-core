#!/usr/bin/env bash
set -euo pipefail

ufo_target=${1:?target is required}
ufo_archive=${2:?archive path is required}

case "$ufo_target" in
  aarch64-apple-darwin) ufo_goos=darwin; ufo_goarch=arm64; ufo_suffix= ;;
  x86_64-apple-darwin) ufo_goos=darwin; ufo_goarch=amd64; ufo_suffix= ;;
  x86_64-unknown-linux-musl) ufo_goos=linux; ufo_goarch=amd64; ufo_suffix= ;;
  aarch64-unknown-linux-musl) ufo_goos=linux; ufo_goarch=arm64; ufo_suffix= ;;
  x86_64-pc-windows-msvc) ufo_goos=windows; ufo_goarch=amd64; ufo_suffix=.exe ;;
  *) echo "unsupported gh target: $ufo_target" >&2; exit 1 ;;
esac

test "$(go env GOVERSION)" = go1.27.0
ufo_host_os=$(go env GOHOSTOS)
ufo_host_arch=$(go env GOHOSTARCH)
ufo_gopath="$(dirname "$ufo_archive")/ufo-gh-gopath"
ufo_go_gopath=$ufo_gopath
if test "$ufo_host_os" = windows; then
  ufo_go_gopath=$(cygpath -w "$ufo_gopath")
fi
mkdir -p "$ufo_gopath"
GOTOOLCHAIN=local GOOS="$ufo_goos" GOARCH="$ufo_goarch" CGO_ENABLED=0 GOPATH="$ufo_go_gopath" \
  GOMODCACHE="$(go env GOMODCACHE)" \
  go install -trimpath -ldflags='-s -w' github.com/cli/cli/v2/cmd/gh@v2.99.0

if test "$ufo_host_os" = "$ufo_goos" && test "$ufo_host_arch" = "$ufo_goarch"; then
  ufo_binary="$ufo_gopath/bin/gh$ufo_suffix"
else
  ufo_binary="$ufo_gopath/bin/${ufo_goos}_${ufo_goarch}/gh$ufo_suffix"
fi
ufo_build=$(go version -m "$ufo_binary")
grep -F 'go1.27.0' <<<"${ufo_build%%$'\n'*}"
grep -F $'mod\tgithub.com/cli/cli/v2\tv2.99.0' <<<"$ufo_build"
mkdir -p "$(dirname "$ufo_archive")"
gzip -9 -c "$ufo_binary" > "$ufo_archive"
gzip -t "$ufo_archive"
