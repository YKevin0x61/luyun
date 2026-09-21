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

# pg_dump 客户端版本必须 ≥ 服务端：它拒绝 dump 比它新的服务端
# （aborting because of server version mismatch），而 PostgreSQL 后端的「更新前
# 备份」正是靠 pg_dump。Debian 12 官方源只有 15，compose 起的却是 postgres:16，
# 所以从 PGDG 源装 16。**升级 compose 里的 PG 大版本时必须同步改这里**，否则更新
# 会卡在 backing_up 阶段（tests/test_docker_compose_contract.py 会拦住这种漂移）。
# 导出标准图时服务端要用 PIL 把「批注」文字画进图片，镜像里没有中文字体的话
# 中文会渲染成方块（圆圈和箭头不受影响）。挑体积小的 wqy-zenhei；候选列表见
# services/hygiene/standards_export.py 的 FONT_CANDIDATES。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-wqy-zenhei \
        tar \
        sqlite3 \
        tini \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
         -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
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
