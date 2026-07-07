# The control-plane image — one image, two roles (`ufo-control api` / `ufo-control operator`).
# It bakes the helm binary and the tenant chart the operator applies.
FROM python:3.12-slim

ARG HELM_VERSION=v3.16.3

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
        | tar -xz -C /tmp \
    && mv /tmp/linux-amd64/helm /usr/local/bin/helm \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/linux-amd64

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# The chart the operator renders + applies; platform.toml points chart_path here.
COPY charts /charts

ENTRYPOINT ["ufo-control"]
