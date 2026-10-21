FROM python:3.12-slim-trixie AS lxml-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates xz-utils autoconf automake libtool pkg-config binutils patch \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir setuptools==84.2.1 wheel==0.48.0
COPY build.sh /lxml-security/build.sh
COPY SHA256SUMS /lxml-security/SHA256SUMS
COPY lxml-hook.patch /lxml-security/lxml-hook.patch
COPY zlib-1.3.2-reset.patch /lxml-security/zlib-1.3.2-reset.patch
COPY libxml2-security.patch /lxml-security/libxml2-security.patch
COPY test-write-state.c /lxml-security/test-write-state.c
RUN sh /lxml-security/build.sh
COPY check.sh /lxml-security/check.sh
COPY check.py /lxml-security/check.py
COPY provenance.json /lxml-security/provenance.json
COPY test-dict.c /lxml-security/test-dict.c
COPY test-valid.c /lxml-security/test-valid.c
COPY test-xpointer.c /lxml-security/test-xpointer.c
COPY test-io.c /lxml-security/test-io.c
COPY test-xinclude.c /lxml-security/test-xinclude.c
RUN sh /lxml-security/check.sh

FROM scratch AS lxml-wheel
COPY --from=lxml-build /out/ /
