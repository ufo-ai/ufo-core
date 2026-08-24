# The preview renderer (RFC 0037): a Rust service that spawns LibreOffice and a pdfium worker as
# bounded children, so the runtime image carries soffice, fonts, and libpdfium beside the binaries.
FROM rust:1-slim AS chef
RUN cargo install cargo-chef --locked --version 0.1.78
WORKDIR /build

FROM chef AS planner
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS build
COPY --from=planner /build/recipe.json recipe.json
RUN cargo chef cook --release --locked --recipe-path recipe.json
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM debian:stable-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-liberation fonts-dejavu-core fonts-noto-core fonts-noto-cjk \
        ffmpeg chromium python3 ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/fetch-pdfium.sh /tmp/fetch-pdfium.sh
RUN sh /tmp/fetch-pdfium.sh /usr/local/lib/pdfium && rm /tmp/fetch-pdfium.sh
ENV UFO_PREVIEW_PDFIUM_LIB=/usr/local/lib/pdfium/libpdfium.so
COPY --from=build /build/target/release/ufo-preview /usr/local/bin/ufo-preview
COPY --from=build /build/target/release/preview-worker /usr/local/bin/preview-worker
COPY scripts/capture-site.py /usr/local/libexec/ufo-site-preview.py
USER nobody
ENTRYPOINT ["tini", "--", "ufo-preview"]
