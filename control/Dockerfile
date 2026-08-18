# The hosted gateway image. It carries one static-ish binary and the client binaries it serves, so
# it needs no Python runtime and no longer extends the ufo distribution.
FROM rust:1-bookworm AS build
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked --bin ufo-control

FROM debian:bookworm-slim
# The binary dials Postgres, STS, SES, WorkOS, and core over TLS. `ca-certificates` supplies the
# public roots; RDS is signed by Amazon's own CA, which is in no public root store, so its bundle is
# fetched here and `UFO_CONTROL_PG_CA_BUNDLE` points the pool at it.
#
# This is not optional. `tokio_postgres` defaults to `sslmode=prefer`, and under `prefer` a TLS
# handshake that *starts* and then fails is a connection error with no fallback to plaintext
# (`connect_tls.rs`). RDS always offers TLS, so without these roots every connection fails
# verification — where asyncpg reached the same instance only because libpq's `prefer` does not
# verify the certificate at all.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL -o /etc/ssl/certs/rds-global-bundle.pem \
         https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
ENV UFO_CONTROL_PG_CA_BUNDLE=/etc/ssl/certs/rds-global-bundle.pem
WORKDIR /app
COPY --from=build /build/target/release/ufo-control /usr/local/bin/ufo-control

# The native terminal client, one binary per target, staged into the build context by the deploy
# workflow; local builds carry the empty directory and the binary route answers 404.
COPY clientbin ./clientbin
ENV UFO_CLIENT_BIN_DIR=/app/clientbin

ENTRYPOINT ["ufo-control"]
