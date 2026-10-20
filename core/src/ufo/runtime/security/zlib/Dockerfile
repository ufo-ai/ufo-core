FROM debian:trixie-slim AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential debhelper autoconf dpkg-dev curl ca-certificates binutils \
    && rm -rf /var/lib/apt/lists/*
COPY . /security
RUN sh /security/build.sh

FROM scratch AS packages
COPY --from=build /out/ /
