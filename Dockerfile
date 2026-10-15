
# syntax=docker/dockerfile:1
# Pin the base image by digest in practice, e.g. python:3.12-slim-bookworm@sha256:...
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---- Stage 1: build wheels (tgcrypto may need a compiler) -------------------
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
# Once requirements.txt is a real hashed lock, add --require-hashes here.
RUN pip wheel --wheel-dir /wheels -r /tmp/requirements.txt

# ---- Stage 2: runtime --------------------------------------------------------
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/bot \
    XDG_CACHE_HOME=/tmp/.cache \
    DENO_DIR=/tmp/deno

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini unzip curl \
 && rm -rf /var/lib/apt/lists/*

# Deno: select the binary for the target architecture.
ARG DENO_VERSION=2.1.4
ARG TARGETARCH

RUN case "$TARGETARCH" in \
      arm64) DENO_ARCH="aarch64" ;; \
      amd64) DENO_ARCH="x86_64" ;; \
      *) echo "Unsupported architecture: $TARGETARCH"; exit 1 ;; \
    esac \
 && curl -fsSL \
      "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${DENO_ARCH}-unknown-linux-gnu.zip" \
      -o /tmp/deno.zip \
 && unzip -o /tmp/deno.zip -d /usr/local/bin \
 && rm /tmp/deno.zip \
 && chmod 755 /usr/local/bin/deno

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin bot

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links /wheels -r requirements.txt \
 && rm -rf /wheels

COPY app/ app/
COPY bot.py .

RUN mkdir -p /data/jobs /data/state /data/session \
 && chown -R bot:bot /data

USER bot

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-m", "app.health"]

# tini reaps zombie yt-dlp/ffmpeg children and forwards SIGTERM.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "app"]
