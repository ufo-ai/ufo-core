# The sandbox cache daemon (RFC 0032): a Rust binary that shells out to git, so the runtime image
# carries git and git-lfs beside it. Built from this crate's source; the deploy runs it as a second
# container in the sandbox-proxy pod, sharing loopback with `ufoctl proxy`.
FROM rust:1-slim AS build
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM debian:stable-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git git-lfs ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/target/release/ufo-cache /usr/local/bin/ufo-cache
ENTRYPOINT ["ufo-cache"]
