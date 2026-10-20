# The sandbox cache daemon: a Rust binary that shells out to git, so the runtime image
# carries git beside it. Built from this crate's source; the deploy runs it as a second container
# in the sandbox-proxy pod, sharing loopback with `ufoctl proxy`.
FROM rust:1-slim-trixie AS chef
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

FROM debian:trixie-slim AS zlib-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential debhelper autoconf dpkg-dev curl ca-certificates binutils \
    && rm -rf /var/lib/apt/lists/*
COPY --from=zlib-security / /security
RUN sh /security/build.sh

FROM debian:trixie-slim AS runtime-packages
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && printf '%s\n' 'deb http://deb.debian.org/debian trixie-backports main' > /etc/apt/sources.list.d/curl-backports.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends -t trixie-backports \
        curl libcurl4t64 libcurl3t64-gnutls \
    && for package in curl libcurl4t64 libcurl4-gnutls; do \
        dpkg --compare-versions "$(dpkg-query -W -f='${Version}' "$package")" ge 8.21.0 || exit 1; \
    done \
    && rm -rf /var/lib/apt/lists/*

COPY --from=zlib-build /out/ /tmp/zlib-packages/
RUN sh /tmp/zlib-packages/install.sh /tmp/zlib-packages && rm -r /tmp/zlib-packages

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

FROM runtime-packages AS runtime
COPY --from=build /build/target/release/ufo-cache /usr/local/bin/ufo-cache
ENTRYPOINT ["ufo-cache"]
