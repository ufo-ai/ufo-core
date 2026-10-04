# The preview renderer: a Rust service that spawns LibreOffice and a pdfium worker as
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

FROM debian:trixie-slim AS runtime-packages
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-liberation fonts-dejavu-core fonts-noto-core fonts-noto-cjk \
        ffmpeg chromium python3 ca-certificates curl tini \
    && printf '%s\n' 'deb http://deb.debian.org/debian trixie-backports main' > /etc/apt/sources.list.d/curl-backports.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends -t trixie-backports \
        curl libcurl4t64 libcurl3t64-gnutls \
    && for package in curl libcurl4t64 libcurl4-gnutls; do \
        dpkg --compare-versions "$(dpkg-query -W -f='${Version}' "$package")" ge 8.21.0 || exit 1; \
    done \
    && rm -rf /var/lib/apt/lists/*

FROM runtime-packages AS runtime
COPY scripts/fetch-pdfium.sh /tmp/fetch-pdfium.sh
RUN sh /tmp/fetch-pdfium.sh /usr/local/lib/pdfium && rm /tmp/fetch-pdfium.sh
ENV UFO_PREVIEW_PDFIUM_LIB=/usr/local/lib/pdfium/libpdfium.so
COPY scripts/seed-soffice-profile.sh /tmp/seed-soffice-profile.sh
RUN sh /tmp/seed-soffice-profile.sh /usr/local/share/ufo-preview/soffice-profile \
    && rm /tmp/seed-soffice-profile.sh
ENV UFO_PREVIEW_SOFFICE_PROFILE=/usr/local/share/ufo-preview/soffice-profile
COPY --from=build /build/target/release/ufo-preview /usr/local/bin/ufo-preview
COPY --from=build /build/target/release/preview-worker /usr/local/bin/preview-worker
COPY scripts/capture-site.py /usr/local/libexec/ufo-site-preview.py
USER nobody
ENTRYPOINT ["tini", "--", "ufo-preview"]
