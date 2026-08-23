# The egress proxy (RFC 0035): a standalone Rust MITM proxy that terminates TLS with in-process
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

FROM debian:stable-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bin/ufo-egress /usr/local/bin/ufo-egress
ENTRYPOINT ["ufo-egress"]
