# The egress proxy: a standalone Rust MITM proxy that terminates TLS with in-process
# minted leaves (rcgen) and asks core `serve` over the control RPC what egress is allowed. It runs
# as the sandbox-proxy pod's main container, replacing `ufoctl proxy`; the cache daemon stays its
# loopback sidecar. No openssl or git at runtime — leaf minting is in-process, and it holds no key,
# no DB, and shells out to nothing.
FROM rust:1-slim AS chef
RUN apt-get update \
    && apt-get install -y --no-install-recommends pkg-config \
    && rm -rf /var/lib/apt/lists/*
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
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked \
    && cp target/release/ufo-egress /usr/local/bin/ufo-egress

FROM debian:stable-slim AS runtime-packages
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

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
COPY --from=build /usr/local/bin/ufo-egress /usr/local/bin/ufo-egress
ENTRYPOINT ["ufo-egress"]
