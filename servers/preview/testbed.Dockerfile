FROM debian:trixie-slim AS libxml2-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential debhelper liblzma-dev pkg-config rename zlib1g-dev \
        libxml2 curl ca-certificates patch xz-utils \
    && rm -rf /var/lib/apt/lists/*
COPY security/libxml2 /security/libxml2
RUN sh /security/libxml2/build.sh

FROM rust:1
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-liberation fonts-dejavu-core fonts-noto-core fonts-noto-cjk \
        ffmpeg chromium python3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=libxml2-build /out/ /tmp/libxml2/
RUN test "$(dpkg-query -W -f='${Version}' libxml2)" = '2.12.7+dfsg+really2.9.14-2.1+deb13u3' \
    && dpkg -i /tmp/libxml2/libxml2_*.deb && rm -rf /tmp/libxml2
COPY scripts/fetch-pdfium.sh /tmp/fetch-pdfium.sh
RUN sh /tmp/fetch-pdfium.sh /usr/local/lib/pdfium && rm /tmp/fetch-pdfium.sh
ENV UFO_PREVIEW_PDFIUM_LIB=/usr/local/lib/pdfium/libpdfium.so
COPY scripts/seed-soffice-profile.sh /tmp/seed-soffice-profile.sh
RUN sh /tmp/seed-soffice-profile.sh /usr/local/share/ufo-preview/soffice-profile \
    && rm /tmp/seed-soffice-profile.sh
ENV UFO_PREVIEW_SOFFICE_PROFILE=/usr/local/share/ufo-preview/soffice-profile
COPY scripts/capture-site.py /usr/local/libexec/ufo-site-preview.py
ENV CARGO_TARGET_DIR=/tmp/target
