FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MINIHERMES_HOME=/data \
    MINIHERMES_WORKSPACE=/workspace

WORKDIR /app

COPY pyproject.toml README.md ./
COPY minihermes ./minihermes
COPY config.sample.json /app/config.sample.json
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN pip install --upgrade pip \
    && pip install -e . \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["telegram"]
