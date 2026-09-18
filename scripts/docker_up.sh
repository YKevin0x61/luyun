#!/usr/bin/env bash
# Build and start the Docker process-shell deploy (ADR 0011).
# Usage (from repo root): ./scripts/docker_up.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.yml"
ENV_EXAMPLE="$ROOT/deploy/.env.docker.example"
ENV_FILE="$ROOT/deploy/.env.docker"

die() { echo "错误: $*" >&2; exit 1; }
log() { echo "==> $*"; }

command -v docker >/dev/null 2>&1 || die "需要 docker"
docker compose version >/dev/null 2>&1 || die "需要 docker compose 插件"

if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "$ENV_EXAMPLE" ]] || die "缺少 $ENV_EXAMPLE"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  log "created $ENV_FILE from example — review before production use"
fi

# Ensure host parent exists (compose creates the mount point, but app/ is clearer).
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a
HOST_PARENT="${LUYUN_HOST_PARENT:-./runtime}"

# 卷路径一律绝对化后导出给 compose：compose 对相对路径按项目目录解析，而项目目录
# 又随调用方式/cwd 变化，曾经把父目录挂到 runtime/app/runtime/ 下的空目录——应用
# 连上一份新建的空库却不报错。相对值统一按 deploy/（compose 文件所在目录）解析。
abs_volume_path() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s' "$ROOT/deploy/$1" ;;
  esac
}

PARENT_ABS="$(abs_volume_path "$HOST_PARENT")"
export LUYUN_HOST_PARENT="$PARENT_ABS"
export LUYUN_PG_DATA="$(abs_volume_path "${LUYUN_PG_DATA:-./runtime/pgdata}")"
export LUYUN_REDIS_DATA="$(abs_volume_path "${LUYUN_REDIS_DATA:-./runtime/redisdata}")"
mkdir -p "$PARENT_ABS/app"

log "docker compose up (parent mount=${PARENT_ABS}, live app=${PARENT_ABS}/app)"
log "volumes: pg=${LUYUN_PG_DATA} redis=${LUYUN_REDIS_DATA}"
cd "$ROOT"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build "$@"
log "started. Admin: http://127.0.0.1:${LUYUN_HOST_PORT:-8000}/admin/"
log "logs: docker compose -f deploy/docker-compose.yml --env-file deploy/.env.docker logs -f"
