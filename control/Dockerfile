# The hosted gateway image extends the matching ufo runtime distribution.
ARG UFO_IMAGE=ufo:latest
FROM ${UFO_IMAGE}

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# The native terminal client, one binary per target, staged into the build context by the deploy
# workflow; local builds carry the empty directory and the binary route answers 404.
COPY clientbin ./clientbin
ENV UFO_CLIENT_BIN_DIR=/app/clientbin

ENTRYPOINT ["ufo-control"]
