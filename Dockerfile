# Multi-stage build — slimmer runtime image (Vangelis 2026-05-06).
#
# Stage 1 installs pip deps into a venv with build-essential (needed
# for any wheels that compile from source). Stage 2 copies just the
# venv into a fresh slim base, skipping build tools entirely. Net:
# image size drops from ~5.3GB to ~3.5GB on the VM.

# ─── Stage 1: builder ────────────────────────────────────────────
FROM python:3.13-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for wheels that compile from source (numpy, pillow, etc.).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# Install everything into a self-contained venv that we'll copy whole
# into stage 2. Using a venv keeps the file paths consistent between
# stages so no relinking is needed.
RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: runtime ────────────────────────────────────────────
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH"

# Runtime apt deps only (no build-essential, no apt cache).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxdamage1 \
    libpango-1.0-0 \
    libcairo2 \
    libgbm1 \
    libasound2 \
    libxrandr2 \
    libxcomposite1 \
    libxshmfence1 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Bring in the prebuilt venv from the builder stage.
COPY --from=builder /venv /venv

# Non-root user. Playwright caches browsers under $HOME so we run the
# install AS appuser, not root.
RUN useradd --create-home appuser \
    && mkdir -p /home/appuser/.u2net \
    && chown appuser:appuser /home/appuser/.u2net
USER appuser
RUN playwright install chromium

WORKDIR /project
COPY --chown=appuser:appuser . /project

EXPOSE 8080
ENTRYPOINT ["python", "-m", "app.main"]
