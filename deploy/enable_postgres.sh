#!/usr/bin/env bash
# 一键把本机切到 PostgreSQL 后端（多店形态，ADR 0084）。
#
# 用法：
#     sudo bash deploy/enable_postgres.sh --dry-run    # 只打印将要做什么
#     sudo bash deploy/enable_postgres.sh              # 实际执行
#
# 做这些事（幂等，可重复执行；已完成的步骤会跳过）：
#     1. 安装 PostgreSQL 并设为开机自启（apt / dnf / yum 自动识别）
#     2. 建库 + 建用户（密码自动生成，随即写入 env.production）
#     3. 应用 migrations/pg/0001_initial_schema.sql
#     4. 停应用 → 备份 SQLite → 迁移数据 → 重置 identity 序列
#     5. 写入 DATABASE_BACKEND=postgres / POSTGRES_DSN
#     6. 启动应用 → 冒烟检查（healthz + 行数比对）
#
# 为什么必须 root：要装系统包、以 postgres 身份建库、改 systemd 服务。
# 更新作业（luyun-update.service）刻意以 luyun 用户跑，就是为了不给 Web 触发的
# 作业这些权限；所以切换动作单独做成这个一次性脚本。
#
# 回滚：把 deploy/env.production 里 DATABASE_BACKEND 改回 sqlite 并重启。
#       本脚本对 data/app.db 全程只读，回滚不丢数据。

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$APP_DIR/deploy/env.production"
VENV_PY="$APP_DIR/.venv/bin/python"
SQLITE_DB="$APP_DIR/data/app.db"
SCHEMA_SQL="$APP_DIR/migrations/pg/0001_initial_schema.sql"
MIGRATE_PY="$APP_DIR/scripts/archive/migrate_sqlite_to_postgres.py"
SERVICE_NAME="${LUYUN_SERVICE:-luyun}"
DB_NAME="${POSTGRES_DB:-luyun}"
DB_USER="${POSTGRES_USER:-luyun}"
DB_HOST="${POSTGRES_HOST:-127.0.0.1}"
DB_PORT="${POSTGRES_PORT:-5432}"
DRY_RUN=0

log()  { printf '\033[1;34m▶\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }
# dry-run 下只回显；否则执行
run() {
  if (( DRY_RUN )); then
    printf '   [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage ;;
    *) die "未知参数：$arg（可用：--dry-run）" ;;
  esac
done

(( DRY_RUN )) && warn "DRY-RUN 模式：只打印，不修改任何东西"

# ── 前置检查 ────────────────────────────────────────────────────────────
# dry-run 只打印预览，不要求 root（读 env 文件时可能仍受权限限制）
if (( ! DRY_RUN )) && [[ $EUID -ne 0 ]]; then
  die "需要 root 权限：sudo bash $0（只想预览可加 --dry-run）"
fi
[[ -f "$ENV_FILE" ]] || die "找不到 $ENV_FILE —— 请先按 deploy/README.md 完成 Bootstrap 安装"
[[ -x "$VENV_PY" ]] || die "找不到 $VENV_PY —— 部署目录不完整"
[[ -f "$SCHEMA_SQL" ]] || die "找不到 $SCHEMA_SQL"
[[ -f "$MIGRATE_PY" ]] || die "找不到 $MIGRATE_PY"

# 应用用户：优先读 systemd unit 里的 User=，否则回退 luyun
APP_USER="$(grep -E '^User=' "/etc/systemd/system/${SERVICE_NAME}.service" 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)"
APP_USER="${APP_USER:-luyun}"
id "$APP_USER" >/dev/null 2>&1 || die "系统用户 $APP_USER 不存在"
log "应用目录: $APP_DIR"
log "应用用户: $APP_USER"
log "目标库:   $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"

# ── 1. PostgreSQL ──────────────────────────────────────────────────────
if command -v pg_isready >/dev/null 2>&1 && pg_isready -q -h "$DB_HOST" -p "$DB_PORT" 2>/dev/null; then
  ok "PostgreSQL 已在运行，跳过安装与启动"
else
  if command -v apt-get >/dev/null 2>&1; then
    log "安装 PostgreSQL（apt）"
    run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    # postgresql-client 提供 pg_dump / pg_isready，备份链路依赖它
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
    die "未识别到包管理器（apt/dnf/yum 都没有）。请手工安装 PostgreSQL 16+ 后重跑本脚本"
  fi

  if command -v systemctl >/dev/null 2>&1; then
    # Debian/Ubuntu 的服务名带版本号，逐个试
    for unit in postgresql postgresql-16 postgresql-15; do
      if systemctl list-unit-files "${unit}.service" >/dev/null 2>&1; then
        run systemctl enable --now "$unit"
        break
      fi
    done
  fi

  # 等库起来
  for _ in $(seq 1 30); do
    pg_isready -q -h "$DB_HOST" -p "$DB_PORT" 2>/dev/null && break
    sleep 1
  done
  pg_isready -q -h "$DB_HOST" -p "$DB_PORT" 2>/dev/null || die "PostgreSQL 安装后仍不可达，请检查服务状态"
  ok "PostgreSQL 已就绪"
fi

# ── 2. 建库 + 建用户 ───────────────────────────────────────────────────
# 以 postgres 超级用户身份操作。库/用户已存在时跳过。
# 最小化安装的 Debian 可能没有 sudo，所以 runuser → sudo → su 依次降级。
as_postgres() {
  if command -v runuser >/dev/null 2>&1; then
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
  DB_PASSWORD=""
else
  DB_PASSWORD="$(openssl rand -base64 24 2>/dev/null | tr -d '/+=' | cut -c1-24 || head -c 24 /dev/urandom | base64 | tr -d '/+=')"
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

# ── 3. 应用 schema ─────────────────────────────────────────────────────
# 密码未知（角色已存在）时无法用 TCP 连，改走本地 socket 以 postgres 身份执行
DSN=""
if [[ -n "$DB_PASSWORD" ]]; then
  DSN="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

log "应用 schema（$SCHEMA_SQL）"
if [[ -n "$DSN" ]]; then
  run psql "$DSN" -v ON_ERROR_STOP=1 -q -f "$SCHEMA_SQL"
  ok "schema 已应用"
else
  # 角色是上一次运行建的，本脚本拿不到它的密码。不要绕 SET ROLE 之类的偏门
  # 路径——直接告诉 operator 该跑什么。
  warn "角色 $DB_USER 已存在，但本脚本不知道它的密码，schema 未应用。请手工执行："
  warn "  psql \"postgresql://${DB_USER}:<密码>@${DB_HOST}:${DB_PORT}/${DB_NAME}\" \\"
  warn "       -v ON_ERROR_STOP=1 -f $SCHEMA_SQL"
fi

# ── 4. 停应用 → 备份 → 迁移数据 ────────────────────────────────────────
MIGRATION_NEEDED=0
if [[ -f "$SQLITE_DB" ]]; then
  if [[ -n "$DSN" ]] && [[ "$(psql "$DSN" -qtAX -c 'SELECT count(*) FROM orders' 2>/dev/null || echo 0)" == "0" ]]; then
    MIGRATION_NEEDED=1
  fi
fi

if (( MIGRATION_NEEDED )); then
  log "停应用（迁移期间源库必须静止）"
  run systemctl stop "$SERVICE_NAME" || warn "停服务失败，继续（可能本就没在跑）"

  BACKUP_DIR="$APP_DIR/backups"
  run mkdir -p "$BACKUP_DIR"
  STAMP="$(date +%Y%m%d_%H%M%S)"
  log "备份源库 → $BACKUP_DIR/pre-pg-migration-$STAMP.db"
  run sqlite3 "$SQLITE_DB" ".backup '$BACKUP_DIR/pre-pg-migration-$STAMP.db'"

  log "迁移数据（先预演）"
  run "$VENV_PY" "$MIGRATE_PY" --dsn "$DSN" --dry-run
  log "迁移数据（实际执行）"
  run "$VENV_PY" "$MIGRATE_PY" --dsn "$DSN" --apply
  ok "数据迁移完成"
else
  ok "目标库已有数据（或源库不存在），跳过迁移"
fi

# ── 5. 写 DATABASE_BACKEND / POSTGRES_DSN ──────────────────────────────
if [[ -z "$DSN" ]]; then
  warn "角色 $DB_USER 已存在，本脚本不知道它的密码。"
  warn "请手工把下面两行写入 $ENV_FILE（密码问当初建库的人）："
  warn "  DATABASE_BACKEND=postgres"
  warn "  POSTGRES_DSN=postgresql://${DB_USER}:<密码>@${DB_HOST}:${DB_PORT}/${DB_NAME}"
else
  log "写入 $ENV_FILE"
  if (( DRY_RUN )); then
    printf '   [dry-run] 设置 DATABASE_BACKEND=postgres 与 POSTGRES_DSN=postgresql://%s:***@%s:%s/%s\n' \
      "$DB_USER" "$DB_HOST" "$DB_PORT" "$DB_NAME"
  else
    # 就地替换或追加，保持其他配置不动
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
    chown "$APP_USER:$APP_USER" "$ENV_FILE" 2>/dev/null || true
    chmod 600 "$ENV_FILE"
  fi
  ok "后端已切到 postgres"
fi

# ── 6. 启动 + 冒烟 ─────────────────────────────────────────────────────
log "启动应用"
run systemctl start "$SERVICE_NAME" || warn "启动失败，见 journalctl -u $SERVICE_NAME"

if (( ! DRY_RUN )); then
  HEALTH_URL="http://127.0.0.1:8000/api/healthz"
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
      ok "冒烟检查通过：$HEALTH_URL"
      break
    fi
    sleep 1
  done
  curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1 \
    || warn "healthz 未通过，请检查 journalctl -u $SERVICE_NAME"

  if [[ -n "$DSN" ]] && [[ -f "$SQLITE_DB" ]]; then
    SRC_ROWS="$(sqlite3 "$SQLITE_DB" 'SELECT count(*) FROM orders' 2>/dev/null || echo '?')"
    DST_ROWS="$(psql "$DSN" -qtAX -c 'SELECT count(*) FROM orders' 2>/dev/null || echo '?')"
    if [[ "$SRC_ROWS" == "$DST_ROWS" ]]; then
      ok "行数比对一致：orders = $SRC_ROWS"
    else
      warn "行数不一致：SQLite=$SRC_ROWS  PostgreSQL=$DST_ROWS —— 请人工核对"
    fi
  fi
fi

echo
ok "完成。后续检查："
echo "    systemctl status $SERVICE_NAME"
echo "    journalctl -u $SERVICE_NAME -n 50"
echo "    curl -s localhost:8000/api/healthz"
echo
echo "回滚：把 $ENV_FILE 里 DATABASE_BACKEND 改回 sqlite，然后 systemctl restart $SERVICE_NAME"
echo "      源库 $SQLITE_DB 全程未被修改。"
