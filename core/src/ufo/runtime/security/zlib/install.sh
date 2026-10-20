#!/bin/sh
set -eu
packages=$1
set --
for name in zlib1g zlib1g-dev libminizip1t64 libminizip-dev minizip; do
    status=$(dpkg-query -W -f='${Status}' "$name" 2>/dev/null || true)
    if [ "$status" = 'install ok installed' ]; then
        test "$(dpkg-query -W -f='${Version}' "$name")" = '1:1.3.dfsg+really1.3.1-1+b1'
        set -- "$@" "$packages"/"$name"_*.deb
    fi
done
test "$#" -gt 0
dpkg -i "$@"
test -z "$(dpkg --audit)"
test "$(dpkg-query -W -f='${Version}' zlib1g)" = '1:1.3.dfsg+really1.3.1-1+ufo1'
mkdir -p /usr/local/share/ufo-zlib
cp "$packages/zlib-provenance.json" /usr/local/share/ufo-zlib/provenance.json
