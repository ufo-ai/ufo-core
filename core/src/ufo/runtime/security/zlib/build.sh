#!/bin/sh
set -eu
security=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test "$(dpkg-query -W -f='${Version}' zlib1g)" = '1:1.3.dfsg+really1.3.1-1+b1'
mkdir -p /build/zlib-source /out
cd /build/zlib-source
while read -r checksum file; do
    curl -fLsS --retry 3 "https://deb.debian.org/debian/pool/main/z/zlib/$file" -o "$file"
done < "$security/SHA256SUMS"
sha256sum -c "$security/SHA256SUMS"
dpkg-source --no-check -x zlib_1.3.dfsg+really1.3.1-1.dsc source
cd source
./configure --static
make -j2
cc -O2 -I. "$security/test-write-state.c" libz.a -o /tmp/test-write-state-before
set +e
/tmp/test-write-state-before
before=$?
set -e
test "$before" = 42
make clean
patch --batch --fuzz=0 -p1 -i "$security/reset-write-state.patch"
{
    printf '%s\n' \
        'zlib (1:1.3.dfsg+really1.3.1-1+ufo1) trixie; urgency=high' \
        '' '  * Reset retained caller input after a gzip write error (CVE-2026-85091).' \
        '' ' -- Marshall <marshall@metalcraft.ai>  Tue, 20 Oct 2026 00:00:00 +0000' ''
    cat debian/changelog
} > debian/changelog.new
mv debian/changelog.new debian/changelog
dpkg-buildpackage --build=binary -Pnobiarch -us -uc -j2
./configure --static
make -j2 test
cc -O2 -I. "$security/test-write-state.c" libz.a -o /tmp/test-write-state-after
/tmp/test-write-state-after
dpkg-deb -x ../zlib1g_*_*.deb /build/zlib-package
library=/build/zlib-package/usr/lib/"$(dpkg-architecture -qDEB_HOST_MULTIARCH)"/libz.so.1.3.1
cc -O2 -I. "$security/test-write-state.c" "$library" -o /tmp/test-write-state-package
LD_LIBRARY_PATH=$(dirname "$library") /tmp/test-write-state-package
readelf -d "$library" | grep -F 'Library soname: [libz.so.1]'
nm -D --defined-only /usr/lib/"$(dpkg-architecture -qDEB_HOST_MULTIARCH)"/libz.so.1 \
    | awk '{print $3}' | sort > /tmp/zlib-before.symbols
nm -D --defined-only "$library" | awk '{print $3}' | sort > /tmp/zlib-after.symbols
diff -u /tmp/zlib-before.symbols /tmp/zlib-after.symbols
cp ../zlib1g_*_*.deb ../zlib1g-dev_*_*.deb ../libminizip1t64_*_*.deb \
    ../libminizip-dev_*_*.deb ../minizip_*_*.deb /out/
cp "$security/provenance.json" /out/zlib-provenance.json
cp "$security/install.sh" /out/
sha256sum /out/*.deb "$library"
