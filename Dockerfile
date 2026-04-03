FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies for Playwright/Chromium
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

WORKDIR /project

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium --with-deps

# Create non-root user
RUN useradd --create-home appuser
USER appuser

COPY --chown=appuser:appuser . /project

EXPOSE 8080

ENTRYPOINT ["python", "app/main.py"]
