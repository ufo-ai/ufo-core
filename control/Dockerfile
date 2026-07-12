# The hosted gateway image extends the matching ufo runtime distribution.
ARG UFO_IMAGE=ufo:latest
FROM ${UFO_IMAGE}

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENTRYPOINT ["ufo-control"]
