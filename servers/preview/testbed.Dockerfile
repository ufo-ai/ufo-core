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
RUN sh /tmp/fetch-pdfium.sh /usr/local/lib/pdfium && rm /tmp/fetch-pdfium.sh
ENV UFO_PREVIEW_PDFIUM_LIB=/usr/local/lib/pdfium/libpdfium.so
COPY scripts/seed-soffice-profile.sh /tmp/seed-soffice-profile.sh
RUN sh /tmp/seed-soffice-profile.sh /usr/local/share/ufo-preview/soffice-profile \
    && rm /tmp/seed-soffice-profile.sh
ENV UFO_PREVIEW_SOFFICE_PROFILE=/usr/local/share/ufo-preview/soffice-profile
COPY scripts/capture-site.py /usr/local/libexec/ufo-site-preview.py
ENV CARGO_TARGET_DIR=/tmp/target
