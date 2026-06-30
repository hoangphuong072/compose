FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MINIHERMES_HOME=/data \
    MINIHERMES_WORKSPACE=/workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libcairo2 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnss3 \
        libpango-1.0-0 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        libxss1 \
        libxtst6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY hermes/pyproject.toml hermes/README.md ./
COPY hermes/minihermes ./minihermes
COPY hermes/config.sample.json /app/config.sample.json
COPY hermes/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN pip install --upgrade pip \
    && pip install -e . "playwright==1.47.0" "camoufox[geoip]==0.4.11" \
    && python -m camoufox fetch \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080 9222

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["serve"]
