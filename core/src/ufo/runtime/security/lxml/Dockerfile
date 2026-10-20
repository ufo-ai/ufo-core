FROM python:3.12-slim-trixie AS lxml-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates xz-utils autoconf automake libtool pkg-config binutils patch \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir setuptools==84.2.1 wheel==0.48.0
COPY . /lxml-security
RUN sh /lxml-security/build.sh

FROM scratch AS lxml-wheel
COPY --from=lxml-build /out/ /
