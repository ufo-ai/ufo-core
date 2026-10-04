# The sandbox cache daemon: a Rust binary that shells out to git, so the runtime image
# carries git beside it. Built from this crate's source; the deploy runs it as a second container
# in the sandbox-proxy pod, sharing loopback with `ufoctl proxy`.
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

FROM debian:stable-slim AS runtime-packages
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

FROM runtime-packages AS runtime
COPY --from=build /build/target/release/ufo-cache /usr/local/bin/ufo-cache
ENTRYPOINT ["ufo-cache"]
