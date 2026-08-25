# syntax=docker/dockerfile:1.7

FROM --platform=$BUILDPLATFORM golang:1.27.0-bookworm AS gh
ARG TARGETARCH
WORKDIR /source
COPY client/scripts/build-gh.sh /source/client/scripts/build-gh.sh
RUN case "$TARGETARCH" in \
      arm64) target=aarch64-unknown-linux-musl ;; \
      amd64) target=x86_64-unknown-linux-musl ;; \
      *) echo "unsupported build architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && client/scripts/build-gh.sh "$target" /ufo-gh.gz

FROM --platform=$TARGETPLATFORM rust:1.88-bookworm AS builder
ARG TARGETARCH
ENV RUSTFLAGS="-C target-feature=+crt-static -C relocation-model=static -C link-arg=-static -C link-arg=-no-pie"
ENV UFO_GH_ARCHIVE=/ufo-gh.gz
RUN apt-get update \
    && apt-get install --yes --no-install-recommends musl-tools \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /source
COPY --from=gh /ufo-gh.gz /ufo-gh.gz
COPY client /source/client
RUN case "$TARGETARCH" in \
      arm64) target=aarch64-unknown-linux-musl ;; \
      amd64) target=x86_64-unknown-linux-musl ;; \
      *) echo "unsupported build architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && rustup target add "$target" \
    && case "$TARGETARCH" in \
      arm64) CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=musl-gcc cargo build --manifest-path client/Cargo.toml --release --target "$target" ;; \
      amd64) CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=musl-gcc cargo build --manifest-path client/Cargo.toml --release --target "$target" ;; \
    esac \
    && cp "client/target/$target/release/ufo" /ufo

FROM scratch AS export
COPY --from=builder /ufo /ufo
