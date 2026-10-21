#!/bin/sh
set -eu
cd /build/lxml-6.1.2
for name in dict valid xpointer io xinclude; do
    cc -O2 -ffunction-sections -fdata-sections \
        -Ibuild/tmp/libxml2-2.14.6 -Ibuild/tmp/libxml2-2.14.6/include \
        -Ibuild/tmp/libxml2/include/libxml2 -Ibuild/tmp/libxml2/include \
        "/lxml-security/test-$name.c" build/tmp/libxml2-2.14.6/.libs/libxml2.a \
        build/tmp/libiconv-1.18/lib/.libs/libiconv.a build/tmp/zlib-1.3.2/libz.a \
        -Wl,--gc-sections -lm -pthread -o "/checks/test-$name"
    timeout 60 "/checks/test-$name"
    printf 'bundled libxml2 security test passed: %s\n' "$name"
done
python -m pip install --no-cache-dir /out/*.whl python-docx==1.2.0
for module in etree objectify; do
    library=$(python -c "import lxml.$module as mod; print(mod.__file__)")
    cc -shared -fPIC -O2 -Dmain=check_zlib_state -Ibuild/tmp/zlib-1.3.2 \
        /lxml-security/test-write-state.c "$library" -o "/checks/state-$module.so"
done
python /lxml-security/check.py
cp /lxml-security/provenance.json /out/
cp /lxml-security/check.py /out/
