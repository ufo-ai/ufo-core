#!/bin/sh
set -eu
mkdir -p /build /out /checks
cd /build
curl -fLsS https://files.pythonhosted.org/packages/ad/a9/970b8fa0ecc4fbf1dfaed0d89bbc1fc1421b25ec26a2038c91e872dc6c8e/lxml-6.1.2.tar.gz -o lxml-6.1.2.tar.gz
curl -fLsS https://ftp.gnu.org/pub/gnu/libiconv/libiconv-1.18.tar.gz -o libiconv-1.18.tar.gz
curl -fLsS https://download.gnome.org/sources/libxml2/2.14/libxml2-2.14.6.tar.xz -o libxml2-2.14.6.tar.xz
curl -fLsS https://download.gnome.org/sources/libxslt/1.1/libxslt-1.1.43.tar.xz -o libxslt-1.1.43.tar.xz
curl -fLsS https://zlib.net/fossils/zlib-1.3.2.tar.gz -o zlib-1.3.2.tar.gz
sha256sum -c /lxml-security/SHA256SUMS
tar -xzf lxml-6.1.2.tar.gz
mkdir -p lxml-6.1.2/libs zlib-original
cp libiconv-1.18.tar.gz libxml2-2.14.6.tar.xz libxslt-1.1.43.tar.xz zlib-1.3.2.tar.gz lxml-6.1.2/libs/
tar -xzf zlib-1.3.2.tar.gz -C zlib-original
cd /build/zlib-original/zlib-1.3.2
./configure --static
make -j2
cc -O2 -I. /lxml-security/test-write-state.c libz.a -o /checks/original-state
set +e
/checks/original-state
before=$?
set -e
test "$before" = 42
cd /build/lxml-6.1.2
cp /lxml-security/zlib-1.3.2-reset.patch .
patch --batch --fuzz=0 -p1 -i /lxml-security/lxml-hook.patch
STATIC_DEPS=true WITHOUT_CYTHON=true \
    ZLIB_VERSION=1.3.2 LIBICONV_VERSION=1.18 LIBXML2_VERSION=2.14.6 LIBXSLT_VERSION=1.1.43 \
    CFLAGS='-O2 -fPIC' MULTICORE=2 \
    python -m pip wheel --no-build-isolation --no-deps . --wheel-dir /out
python -m pip install --no-cache-dir /out/*.whl python-docx==1.2.0
for module in etree objectify; do
    library=$(python -c "import lxml.$module as mod; print(mod.__file__)")
    cc -shared -fPIC -O2 -Dmain=check_zlib_state -Ibuild/tmp/zlib-1.3.2 \
        /lxml-security/test-write-state.c "$library" -o "/checks/state-$module.so"
done
python /lxml-security/check.py
cp /lxml-security/provenance.json /out/
cp /lxml-security/check.py /out/
