#!/usr/bin/env bash
# 一键把本机切到 PostgreSQL 后端（多店形态，ADR 0084）。
#
# 用法：
#     sudo bash deploy/enable_postgres.sh --dry-run    # 只打印将要做什么
#     sudo bash deploy/enable_postgres.sh              # 实际执行
#
# 支持两种部署形态，自动识别：
#   systemd（裸机/VM）—— systemctl 停启 luyun，PG 装在本机
#   Docker（compose）  —— docker stop/start 容器，PG 起在 compose 的 pg profile
#
# 做这些事（幂等，可重复执行；已完成的步骤会跳过）：
#     systemd: 装 PostgreSQL → 建库建用户 → 应用 schema → 停应用 → 备份 → 迁移
#              → 写 env.production → 启动 → 冒烟
#     docker:  重建镜像（为了拿到 pg_dump）→ 起 postgres 服务 → 建库 → 应用
#              schema → 停应用 → 备份 → 迁移（容器内执行）→ 写 env.production
#              → 启动 → 冒烟
#
# 为什么必须 root：装系统包、以 postgres 身份建库、改 systemd/docker 状态。
# 更新作业（luyun-update.service）刻意以非特权用户跑，这些权限不给它。
#
# 回滚：把 deploy/env.production 里 DATABASE_BACKEND 改回 sqlite 并重启。
#       本脚本对 data/app.db 全程只读，回滚不丢数据。

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$APP_DIR/deploy/env.production"
COMPOSE_FILE="$APP_DIR/deploy/docker-compose.yml"
VENV_PY="$APP_DIR/.venv/bin/python"
SQLITE_DB="$APP_DIR/data/app.db"
SCHEMA_SQL="$APP_DIR/migrations/pg/0001_initial_schema.sql"
MIGRATE_REL="scripts/archive/migrate_sqlite_to_postgres.py"
SERVICE_NAME="${LUYUN_SERVICE:-luyun}"
CONTAINER_NAME="${LUYUN_DOCKER_CONTAINER:-luyun}"
PG_CONTAINER="${LUYUN_PG_CONTAINER:-luyun-postgres}"
DB_NAME="${POSTGRES_DB:-luyun}"
DB_USER="${POSTGRES_USER:-luyun}"
DB_PORT="${POSTGRES_PORT:-5432}"
DRY_RUN=0
MODE=""
CONTAINER=""

log()  { printf '\033[1;34m▶\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }
run() {
  if (( DRY_RUN )); then
    printf '   [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

usage() { sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage ;;
    *) die "未知参数：$arg（可用：--dry-run）" ;;
  esac
done

(( DRY_RUN )) && warn "DRY-RUN 模式：只打印，不修改任何东西"

if (( ! DRY_RUN )) && [[ $EUID -ne 0 ]]; then
  die "需要 root 权限：sudo bash $0（只想预览可加 --dry-run）"
fi

# ── 部署形态识别 ───────────────────────────────────────────────────────
detect_container() {
  local c
  if command -v docker >/dev/null 2>&1; then
    for c in "$CONTAINER_NAME" luyun; do
      if docker inspect "$c" >/dev/null 2>&1; then printf '%s' "$c"; return 0; fi
    done
    c="$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -i 'luyun' | head -n 1 || true)"
    if [[ -n "$c" ]]; then printf '%s' "$c"; return 0; fi
  fi
  return 1
}

if [[ -n "${LUYUN_DEPLOY_MODE:-}" ]]; then
  MODE="$LUYUN_DEPLOY_MODE"
elif CONTAINER="$(detect_container)"; then
  MODE="docker"
elif command -v systemctl >/dev/null 2>&1; then
  MODE="systemd"
else
  die "无法识别部署形态（既没有 luyun 容器也没有 systemctl）。可用 LUYUN_DEPLOY_MODE=systemd|docker 显式指定"
fi
[[ "$MODE" == "docker" ]] && CONTAINER="${CONTAINER:-$(detect_container || true)}"

log "应用目录: $APP_DIR"
log "部署形态: $MODE${CONTAINER:+（容器 $CONTAINER）}"
log "目标库:   $DB_USER@$DB_NAME"

# ── 平台操作抽象 ───────────────────────────────────────────────────────
app_stop() {
  case "$MODE" in
    systemd) systemctl stop "$SERVICE_NAME" || warn "停服务失败（可能本就没在跑）" ;;
    docker)  docker stop "$CONTAINER" >/dev/null || warn "停容器失败（可能本就没在跑）" ;;
  esac
}

app_start() {
  case "$MODE" in
    systemd) systemctl start "$SERVICE_NAME" || warn "启动失败，见 journalctl -u $SERVICE_NAME" ;;
    docker)  docker start "$CONTAINER" >/dev/null || warn "启动容器失败，见 docker logs $CONTAINER" ;;
  esac
}

# 在应用运行环境里执行命令（Docker 下进容器）
in_app() {
  case "$MODE" in
    systemd) ( cd "$APP_DIR" && "$VENV_PY" "$@" ) ;;
    docker)  docker exec -w /srv/luyun/app "$CONTAINER" .venv/bin/python "$@" ;;
  esac
}

# app-run 相对路径（Docker 下容器内路径固定为 /srv/luyun/app）
app_path() {
  case "$MODE" in
    systemd) printf '%s/%s' "$APP_DIR" "$1" ;;
    docker)  printf '/srv/luyun/app/%s' "$1" ;;
  esac
}

# ── 前置检查 ───────────────────────────────────────────────────────────
[[ -f "$ENV_FILE" ]] || die "找不到 $ENV_FILE —— 请先完成 Bootstrap 安装"
[[ -f "$SCHEMA_SQL" ]] || die "找不到 $SCHEMA_SQL"
if [[ "$MODE" == "systemd" ]]; then
  [[ -x "$VENV_PY" ]] || die "找不到 $VENV_PY —— 部署目录不完整"
  APP_USER="$(grep -E '^User=' "/etc/systemd/system/${SERVICE_NAME}.service" 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)"
  APP_USER="${APP_USER:-luyun}"
  id "$APP_USER" >/dev/null 2>&1 || die "系统用户 $APP_USER 不存在"
else
  [[ -n "$CONTAINER" ]] || die "Docker 形态未找到 luyun 容器"
  [[ -f "$COMPOSE_FILE" ]] || die "找不到 $COMPOSE_FILE（Docker 形态需要 compose 文件来起 PostgreSQL）"
  APP_USER="$(docker inspect -f '{{.Config.User}}' "$CONTAINER" 2>/dev/null || true)"
  APP_USER="${APP_USER:-root}"
fi

# ══════════════════════════════════════════════════════════════════════
# Docker 形态专有：重建镜像 + 起 postgres 服务
# ══════════════════════════════════════════════════════════════════════
if [[ "$MODE" == "docker" ]]; then
  # 关键：0.6.0 的 Dockerfile 才装了 postgresql-client（pg_dump/psql）。
  # 而「系统更新」只替换 app 树、不重建镜像，所以已部署机器升级后镜像里
  # 没有 pg_dump —— 不重建的话，切到 PG 后更新作业的 backing_up 阶段会因
  # FileNotFoundError 直接失败，把后续所有更新都堵死。
  log "重建镜像（为了拿到 pg_dump / psql）"
  if command -v docker >/dev/null 2>&1; then
    if (( DRY_RUN )); then
      printf '   [dry-run] docker compose -f %s build\n' "$COMPOSE_FILE"
    else
      if docker compose -f "$COMPOSE_FILE" build 2>&1 | tail -5; then
        ok "镜像已重建"
      else
        die "镜像重建失败 —— 不重建就没有 pg_dump，切 PG 后备份会失败。请先修好构建"
      fi
    fi
  fi

  log "启动 PostgreSQL（compose pg profile）"
  if (( DRY_RUN )); then
    printf '   [dry-run] docker compose -f %s --profile pg up -d postgres\n' "$COMPOSE_FILE"
  else
    if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
      die "启用 pg profile 需要 POSTGRES_PASSWORD。请先 export POSTGRES_PASSWORD=<强密码> 再跑本脚本"
    fi
    docker compose -f "$COMPOSE_FILE" --profile pg up -d postgres 2>&1 | tail -3
  fi

  # 等库就绪
  if (( ! DRY_RUN )); then
    for _ in $(seq 1 60); do
      if docker exec "$PG_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    docker exec "$PG_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1 \
      || die "PostgreSQL 容器未就绪，检查 docker logs $PG_CONTAINER"
    ok "PostgreSQL 已就绪"
  fi

  # Docker 形态下容器内用服务名互访
  DB_HOST="postgres"
  DB_PASSWORD="${POSTGRES_PASSWORD:-}"
else
  # ── systemd 形态：装 PG ──
  if command -v pg_isready >/dev/null 2>&1 && pg_isready -q 2>/dev/null; then
    ok "PostgreSQL 已在运行，跳过安装"
  else
    if command -v apt-get >/dev/null 2>&1; then
      log "安装 PostgreSQL（apt）"
      run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
      run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-client
    elif command -v dnf >/dev/null 2>&1; then
      log "安装 PostgreSQL（dnf）"
      run dnf install -y -q postgresql-server postgresql
      run postgresql-setup --initdb || true
    elif command -v yum >/dev/null 2>&1; then
      log "安装 PostgreSQL（yum）"
      run yum install -y -q postgresql-server postgresql
      run postgresql-setup initdb || true
    else
      die "未识别到包管理器。请手工安装 PostgreSQL 16+ 后重跑"
    fi
    if command -v systemctl >/dev/null 2>&1; then
      for unit in postgresql postgresql-16 postgresql-15; do
        if systemctl list-unit-files "${unit}.service" >/dev/null 2>&1; then
          run systemctl enable --now "$unit"
          break
        fi
      done
    fi
    for _ in $(seq 1 30); do
      pg_isready -q 2>/dev/null && break
      sleep 1
    done
    pg_isready -q 2>/dev/null || die "PostgreSQL 安装后仍不可达"
    ok "PostgreSQL 已就绪"
  fi
  DB_HOST="${POSTGRES_HOST:-127.0.0.1}"
fi

# ── 建库 + 建用户 ──────────────────────────────────────────────────────
as_postgres() {
  if [[ "$MODE" == "docker" ]]; then
    docker exec -i "$PG_CONTAINER" "$@"
  elif command -v runuser >/dev/null 2>&1; then
    runuser -u postgres -- "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u postgres "$@"
  else
    su postgres -s /bin/sh -c "$(printf '%q ' "$@")"
  fi
}

psql_super() { as_postgres psql -v ON_ERROR_STOP=1 -qtAX -c "$1"; }

if [[ "$(psql_super "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'")" == "1" ]]; then
  ok "角色 $DB_USER 已存在"
  if [[ "$MODE" == "docker" ]]; then
    DB_PASSWORD="${POSTGRES_PASSWORD:-}"
    [[ -n "$DB_PASSWORD" ]] || die "角色已存在但未提供 POSTGRES_PASSWORD，无法构造 DSN"
  else
    DB_PASSWORD=""
  fi
else
  if [[ "$MODE" == "docker" ]]; then
    DB_PASSWORD="${POSTGRES_PASSWORD:?Docker 形态需要 POSTGRES_PASSWORD}"
  else
    DB_PASSWORD="$(openssl rand -base64 24 2>/dev/null | tr -d '/+=' | cut -c1-24 || head -c 24 /dev/urandom | base64 | tr -d '/+=')"
  fi
  log "创建角色 $DB_USER"
  run as_postgres psql -v ON_ERROR_STOP=1 -qtAX \
    -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
fi

if [[ "$(psql_super "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")" == "1" ]]; then
  ok "数据库 $DB_NAME 已存在"
else
  log "创建数据库 $DB_NAME"
  run as_postgres psql -v ON_ERROR_STOP=1 -qtAX \
    -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
fi

DSN=""
if [[ -n "$DB_PASSWORD" ]]; then
  DSN="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

# ── 应用 schema ────────────────────────────────────────────────────────
log "应用 schema"
if [[ -n "$DSN" ]]; then
  if [[ "$MODE" == "docker" ]]; then
    # schema 在宿主机，容器内看不到路径 —— 用 stdin 灌进去
    run docker exec -i "$CONTAINER" psql "$DSN" -v ON_ERROR_STOP=1 -q -f - < "$SCHEMA_SQL"
  else
    run psql "$DSN" -v ON_ERROR_STOP=1 -q -f "$SCHEMA_SQL"
  fi
  ok "schema 已应用"
else
  warn "角色 $DB_USER 已存在但未知密码，schema 未应用。请手工执行："
  warn "  psql \"postgresql://${DB_USER}:<密码>@${DB_HOST}:${DB_PORT}/${DB_NAME}\" -f $SCHEMA_SQL"
fi

# ── 停应用 → 备份 → 迁移 ───────────────────────────────────────────────
if [[ -f "$SQLITE_DB" && -n "$DSN" ]]; then
  # 目标库 orders 行数（已迁移过就跳过，保证幂等）
  if [[ "$MODE" == "docker" ]]; then
    HAS_DATA="$(docker exec "$CONTAINER" psql "$DSN" -qtAX -c 'SELECT count(*) FROM orders' 2>/dev/null || echo 0)"
  else
    HAS_DATA="$(psql "$DSN" -qtAX -c 'SELECT count(*) FROM orders' 2>/dev/null || echo 0)"
  fi

  if [[ "$HAS_DATA" == "0" ]]; then
    log "停应用（迁移期间源库必须静止）"
    app_stop
    run mkdir -p "$APP_DIR/backups"
    STAMP="$(date +%Y%m%d_%H%M%S)"
    log "备份源库 → backups/pre-pg-migration-$STAMP.db"
    run sqlite3 "$SQLITE_DB" ".backup '$APP_DIR/backups/pre-pg-migration-$STAMP.db'"
    log "预演迁移"
    run in_app "$(app_path "$MIGRATE_REL")" --dsn "$DSN" --dry-run
    log "执行迁移"
    run in_app "$(app_path "$MIGRATE_REL")" --dsn "$DSN" --apply
    ok "数据迁移完成"
  else
    ok "目标库已有数据（$HAS_DATA 行 orders），跳过迁移"
  fi
else
  ok "没有源库或未得到 DSN，跳过迁移"
fi

# ── 写 DATABASE_BACKEND / POSTGRES_DSN ─────────────────────────────────
if [[ -z "$DSN" ]]; then
  warn "未生成 DSN，请手工把下面两行写入 $ENV_FILE："
  warn "  DATABASE_BACKEND=postgres"
  warn "  POSTGRES_DSN=postgresql://${DB_USER}:<密码>@${DB_HOST}:${DB_PORT}/${DB_NAME}"
else
  log "写入 $ENV_FILE"
  if (( DRY_RUN )); then
    printf '   [dry-run] DATABASE_BACKEND=postgres\n'
    printf '   [dry-run] POSTGRES_DSN=postgresql://%s:***@%s:%s/%s\n' "$DB_USER" "$DB_HOST" "$DB_PORT" "$DB_NAME"
  else
    if grep -qE '^DATABASE_BACKEND=' "$ENV_FILE"; then
      sed -i "s|^DATABASE_BACKEND=.*|DATABASE_BACKEND=postgres|" "$ENV_FILE"
    else
      printf '\nDATABASE_BACKEND=postgres\n' >> "$ENV_FILE"
    fi
    if grep -qE '^POSTGRES_DSN=' "$ENV_FILE"; then
      sed -i "s|^POSTGRES_DSN=.*|POSTGRES_DSN=${DSN}|" "$ENV_FILE"
    else
      printf 'POSTGRES_DSN=%s\n' "$DSN" >> "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE" || true
    ok "后端已切到 postgres"
  fi
fi

# ── 启动 + 冒烟 ────────────────────────────────────────────────────────
log "启动应用"
app_start

if (( ! DRY_RUN )); then
  HEALTH_URL="http://127.0.0.1:8000/api/healthz"
  healthy=0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then healthy=1; break; fi
    sleep 1
  done
  if (( healthy )); then
    ok "冒烟检查通过：$HEALTH_URL"
  else
    warn "healthz 未通过。systemd: journalctl -u $SERVICE_NAME；docker: docker logs $CONTAINER"
  fi

  if [[ -n "$DSN" && -f "$SQLITE_DB" ]]; then
    SRC_ROWS="$(sqlite3 "$SQLITE_DB" 'SELECT count(*) FROM orders' 2>/dev/null || echo '?')"
    if [[ "$MODE" == "docker" ]]; then
      DST_ROWS="$(docker exec "$CONTAINER" psql "$DSN" -qtAX -c 'SELECT count(*) FROM orders' 2>/dev/null || echo '?')"
    else
      DST_ROWS="$(psql "$DSN" -qtAX -c 'SELECT count(*) FROM orders' 2>/dev/null || echo '?')"
    fi
    if [[ "$SRC_ROWS" == "$DST_ROWS" ]]; then
      ok "行数比对一致：orders = $SRC_ROWS"
    else
      warn "行数不一致：SQLite=$SRC_ROWS  PostgreSQL=$DST_ROWS —— 请人工核对"
    fi
  fi
fi

echo
ok "完成。后续检查："
case "$MODE" in
  systemd) echo "    systemctl status $SERVICE_NAME && journalctl -u $SERVICE_NAME -n 50" ;;
  docker)  echo "    docker ps && docker logs --tail 50 $CONTAINER" ;;
esac
echo "    curl -s localhost:8000/api/healthz"
echo
echo "回滚：把 $ENV_FILE 里 DATABASE_BACKEND 改回 sqlite 并重启应用。"
echo "      源库 $SQLITE_DB 全程未被修改。"
