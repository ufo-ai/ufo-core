FROM debian:trixie-slim AS zlib-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential debhelper autoconf dpkg-dev curl ca-certificates binutils \
    && rm -rf /var/lib/apt/lists/*
COPY --from=zlib-security / /security
RUN sh /security/build.sh

FROM rust:1
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-liberation fonts-dejavu-core fonts-noto-core fonts-noto-cjk \
        ffmpeg chromium python3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=zlib-build /out/ /tmp/zlib-packages/
RUN sh /tmp/zlib-packages/install.sh /tmp/zlib-packages && rm -r /tmp/zlib-packages
COPY scripts/fetch-pdfium.sh /tmp/fetch-pdfium.sh
RUN set -eu; \
    find /usr/lib -type f \( -name EUC-JISX0213.so -o -name SHIFT_JISX0213.so \) -delete; \
    find /usr/lib -type f \( -name gconv-modules -o -name gconv-modules-extra.conf \) -exec sed -i '/EUC-JISX0213\|SHIFT_JISX0213/d' {} +; \
    iconvconfig; \
    for encoding in EUC-JISX0213 EUC_JISX0213 SHIFT_JISX0213 SHIFT-JISX0213; do \
        if LC_ALL=C iconv -f "$encoding" -t UTF-8 </dev/null 2>/tmp/gconv-error; then exit 1; fi; \
        grep -q 'is not supported' /tmp/gconv-error; done; \
    rm /tmp/gconv-error; \
    test "$(printf '\202\240' | iconv -f SHIFT_JIS -t UTF-8)" = "$(printf '\343\201\202')"; \
    test "$(printf '\244\242' | iconv -f EUC-JP -t UTF-8)" = "$(printf '\343\201\202')"; \
    test "$(printf x | iconv -f ISO-2022-JP-3 -t UTF-8)" = x; \
    test "$(printf x | iconv -f UTF-8 -t UTF-8)" = x
RUN sh /tmp/fetch-pdfium.sh /usr/local/lib/pdfium && rm /tmp/fetch-pdfium.sh
ENV UFO_PREVIEW_PDFIUM_LIB=/usr/local/lib/pdfium/libpdfium.so
COPY scripts/seed-soffice-profile.sh /tmp/seed-soffice-profile.sh
RUN sh /tmp/seed-soffice-profile.sh /usr/local/share/ufo-preview/soffice-profile \
    && rm /tmp/seed-soffice-profile.sh
ENV UFO_PREVIEW_SOFFICE_PROFILE=/usr/local/share/ufo-preview/soffice-profile
COPY scripts/capture-site.py /usr/local/libexec/ufo-site-preview.py
ENV CARGO_TARGET_DIR=/tmp/target
