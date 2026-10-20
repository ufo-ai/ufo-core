#!/bin/sh
set -eu
security=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test "$(dpkg-query -W -f='${Version}' libfreetype6)" = '2.13.3+dfsg-1+deb13u1'
mkdir -p /build/freetype-source /out
cd /build/freetype-source
while read -r checksum file; do
    curl -fLsS --retry 3 "https://deb.debian.org/debian/pool/main/f/freetype/$file" -o "$file"
done < "$security/SHA256SUMS"
sha256sum -c "$security/SHA256SUMS"
dpkg-source --no-check -x freetype_2.13.3+dfsg-1+deb13u1.dsc source
cd source
patch --batch --fuzz=0 -p1 -i "$security/CVE-2026-95512.patch"
sed -i '1s/deb13u1)/deb13u1+ufo1)/' debian/changelog
dpkg-buildpackage --build=any -Ppkg.freetype.nodemos,noudeb -us -uc -j2
cc -O2 -ffunction-sections -fdata-sections -DFT2_BUILD_LIBRARY -I. -Iinclude \
    "$security/test-cid.c" objs/.libs/libfreetype.a \
    -Wl,--gc-sections -lpng16 -lbrotlidec -lbz2 -lz -lm -o /tmp/test-cid
/tmp/test-cid
printf 'freetype CID allocation tests passed\n'
cc -O2 -Iinclude "$security/test-font.c" objs/.libs/libfreetype.a \
    -lpng16 -lbrotlidec -lbz2 -lz -lm -o /tmp/test-font
/tmp/test-font
printf 'freetype normal font render passed\n'
library=objs/.libs/libfreetype.so.6.20.2
readelf -d "$library" | grep -F 'Library soname: [libfreetype.so.6]'
nm -D --defined-only /usr/lib/"$(dpkg-architecture -qDEB_HOST_MULTIARCH)"/libfreetype.so.6 \
    | awk '{print $3}' | sort > /tmp/freetype-before.symbols
nm -D --defined-only "$library" | awk '{print $3}' | sort > /tmp/freetype-after.symbols
diff -u /tmp/freetype-before.symbols /tmp/freetype-after.symbols
cp ../libfreetype6_*_*.deb /out/
mkdir /tmp/freetype-package
dpkg-deb --extract /out/libfreetype6_*.deb /tmp/freetype-package
(cd /tmp/freetype-package && sha256sum usr/lib/"$(dpkg-architecture -qDEB_HOST_MULTIARCH)"/libfreetype.so.6.20.2) \
    | sed 's@  usr/@  /usr/@' > /out/freetype.sha256
cp "$security/provenance.json" /out/freetype-provenance.json
