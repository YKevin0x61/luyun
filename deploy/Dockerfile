# Runtime image for Docker / 1Panel process-shell deploy (ADR 0011).
# Delivery truth remains the GitHub Release Bundle bind-mounted under
# /srv/luyun/app — this image is Python + Playwright OS deps, not the app tree.
FROM python:3.11-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    LUYUN_APP_DIR=/srv/luyun/app \
    LUYUN_DEPLOY_MODE=docker

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tar \
        sqlite3 \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps + Chromium into the image so first boot and scraper
# have browsers even before the bind-mounted .venv exists.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -f /tmp/requirements.txt

COPY deploy/docker-entrypoint.sh /usr/local/bin/luyun-docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/luyun-docker-entrypoint.sh

WORKDIR /srv/luyun/app
EXPOSE 8000

ENTRYPOINT ["tini", "--", "/usr/local/bin/luyun-docker-entrypoint.sh"]
CMD ["serve"]
