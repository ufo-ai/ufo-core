#!/bin/sh
set -eu
security=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test "$(dpkg-query -W -f='${Version}' libxml2)" = '2.12.7+dfsg+really2.9.14-2.1+deb13u3'
mkdir -p /build/libxml2-source /out
cd /build/libxml2-source
while read -r checksum file; do
    curl -fLsS --retry 3 "https://deb.debian.org/debian/pool/main/libx/libxml2/$file" -o "$file"
done < "$security/SHA256SUMS"
sha256sum -c "$security/SHA256SUMS"
dpkg-source --no-check -x libxml2_2.12.7+dfsg+really2.9.14-2.1+deb13u3.dsc source
curl -fLsS --retry 3 https://download.gnome.org/sources/libxml2/2.9/libxml2-2.9.14.tar.xz -o tests.tar.xz
printf '%s\n' '60d74a257d1ccec0475e749cba2f21559e48139efba6ff28224357c7c798dfee  tests.tar.xz' | sha256sum -c -
tar --skip-old-files -xf tests.tar.xz --strip-components=1 -C source \
    libxml2-2.9.14/test libxml2-2.9.14/result
cd source
while read -r file; do
    patch --batch --fuzz=0 -p1 -i "$security/$file"
done < "$security/series"
sed -i '1s/deb13u3)/deb13u3+ufo2)/' debian/changelog
dpkg-buildpackage --build=any -Pnopython -us -uc -j2
make -C builddir/main -j2 check
for name in dict valid xpointer io xinclude catalog; do
    cc -O2 -ffunction-sections -fdata-sections -Ibuilddir/main -Ibuilddir/main/include -I. -Iinclude \
        "$security/test-$name.c" builddir/main/.libs/libxml2.a \
        -Wl,--gc-sections -lz -llzma -lm -pthread -o "/tmp/test-$name"
    "/tmp/test-$name"
    printf 'libxml2 security test passed: %s\n' "$name"
done
library=builddir/main/.libs/libxml2.so.2.9.14
readelf -d "$library" | grep -F 'Library soname: [libxml2.so.2]'
nm -D --defined-only /usr/lib/"$(dpkg-architecture -qDEB_HOST_MULTIARCH)"/libxml2.so.2 \
    | awk '{print $3}' | sort > /tmp/libxml2-before.symbols
nm -D --defined-only "$library" | awk '{print $3}' | sort > /tmp/libxml2-after.symbols
diff -u /tmp/libxml2-before.symbols /tmp/libxml2-after.symbols
cp ../libxml2_*_*.deb /out/
mkdir /tmp/libxml2-package
dpkg-deb --extract /out/libxml2_*.deb /tmp/libxml2-package
(cd /tmp/libxml2-package && sha256sum usr/lib/"$(dpkg-architecture -qDEB_HOST_MULTIARCH)"/libxml2.so.2.9.14) \
    | sed 's@  usr/@  /usr/@' > /out/libxml2.sha256
cp "$security/provenance.json" /out/libxml2-provenance.json
