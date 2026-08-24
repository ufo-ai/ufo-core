FROM rust:1
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-liberation fonts-dejavu-core fonts-noto-core fonts-noto-cjk \
        ffmpeg chromium python3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/fetch-pdfium.sh /tmp/fetch-pdfium.sh
RUN sh /tmp/fetch-pdfium.sh /usr/local/lib/pdfium && rm /tmp/fetch-pdfium.sh
ENV UFO_PREVIEW_PDFIUM_LIB=/usr/local/lib/pdfium/libpdfium.so
COPY scripts/capture-site.py /usr/local/libexec/ufo-site-preview.py
ENV CARGO_TARGET_DIR=/tmp/target
