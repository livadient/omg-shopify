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
    PATH="/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Bring in the prebuilt venv from the builder stage so the playwright CLI
# is on PATH for the install step below.
COPY --from=builder /venv /venv

# Install Chromium AND its system deps via Playwright's canonical list
# (`--with-deps`). Hand-maintaining the apt list bit us repeatedly:
# every Playwright/Chromium upgrade adds another lib we forgot
# (libnspr4, libatspi2.0-0, libxkbcommon0, libdbus-1-3, libxfixes3...).
# Microsoft maintains the right list per OS version — defer to them.
# Browsers go to a system path (/ms-playwright) so the non-root appuser
# can read them after the chown below.
RUN apt-get update \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. Give appuser ownership of the browser cache.
RUN useradd --create-home appuser \
    && mkdir -p /home/appuser/.u2net \
    && chown appuser:appuser /home/appuser/.u2net \
    && chown -R appuser:appuser /ms-playwright
USER appuser

WORKDIR /project
COPY --chown=appuser:appuser . /project

EXPOSE 8080
ENTRYPOINT ["python", "-m", "app.main"]
