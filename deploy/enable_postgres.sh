#!/usr/bin/env bash
# 一键把本机切到 PostgreSQL 后端（多店形态，ADR 0084）。
#
# 用法：
#     sudo bash deploy/enable_postgres.sh --dry-run    # 只打印将要做什么
#     sudo bash deploy/enable_postgres.sh              # 实际执行
#
# 支持两种部署形态，自动识别：
#   systemd（裸机/VM）—— systemctl 停启 luyun，PG 与 Redis 都装在本机
#   Docker（compose）  —— docker stop/start 容器，PG/Redis 都起在 compose 里
#
# 做这些事（幂等，可重复执行；已完成的步骤会跳过）：
#     systemd: 装 PostgreSQL + Redis → 建库建用户 → 应用 schema → 停应用 → 备份
#              → 迁移 → 写 env.production（含 REDIS_URL）→ 启动 → 冒烟
#     docker:  重建镜像（为了拿到 pg_dump）→ 起 postgres + redis 服务 → 建库 →
#              应用 schema → 停应用 → 备份 → 迁移（容器内执行）→ 写
#              env.production（REDIS_URL 用服务名）→ 启动 → 冒烟
#
# Redis 是部署必需组件（realtime nudge 的跨进程广播走它）：应用在没有 REDIS_URL
# 时启动即失败，只是「暂时连不上」才退避重连、不拦启动。所以 systemd 形态顺手把
# redis-server/redis 装上并 enable --now；docker 形态由 compose 的 redis 服务提供。
# 但本脚本的主职责是搬库 —— Redis 装不上/起不来只警告并继续，最后在总结里显著
# 提示「Redis 未就绪，应用起不来」。
#
# 为什么必须 root：装系统包、以 postgres 身份建库、改 systemd/docker 状态。
# 更新作业（luyun-update.service）刻意以非特权用户跑，这些权限不给它。
#
# 回滚：把 deploy/env.production 里 DATABASE_BACKEND 改回 sqlite 并重启。
#       本脚本对 data/app.db 全程只读，回滚不丢数据。
#       Redis 这一项不参与回滚：REDIS_URL 是必需的，删空它应用反而起不来；不想
#       用本机 Redis 时，把这一项改成可用实例的地址（或让 compose 提供实例）。
#       本脚本对 Redis 只做「装 + enable --now + 写 URL」，不动任何已有数据。

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
# Redis（realtime nudge 的跨进程广播，部署必需）。默认是「同机部署」：
# Redis 装在本机、应用在宿主机上跑。环境变量 REDIS_URL 可以覆盖这个默认值
# （Redis 在别的机器上，或 Docker 形态要用 compose 服务名 redis://redis:6379/0）。
REDIS_URL_DEFAULT="redis://127.0.0.1:6379/0"
REDIS_URL_VALUE="${REDIS_URL:-$REDIS_URL_DEFAULT}"
REDIS_UNIT=""
# Redis 就绪状态：ok = 已确认可用（或 dry-run 计划可行）；fail = 确认没起来
# （总结里要显著提示「应用起不来」）。
REDIS_STATUS="ok"
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

usage() { sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

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

# ── PostgreSQL 辅助：探活 / pg_hba 认证 ────────────────────────────────
# 以 postgres 超级用户身份执行命令（建库建用户、探活、reload 都用它）。
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

# 探活查询连 postgres 库（它一定存在），不依赖目标库已建好。
pg_query() {
  case "$MODE" in
    docker) docker exec "$PG_CONTAINER" psql -U "$DB_USER" -d postgres -qtAX -c "$1" 2>/dev/null ;;
    *)      as_postgres psql -d postgres -qtAX -c "$1" 2>/dev/null ;;
  esac
}

pg_probe() { [[ "$(pg_query 'SELECT 1' || true)" == "1" ]]; }

# 等 PG 真正稳定可查。
#
# 不能只用 pg_isready：它只回答「postmaster 是否接受连接」。postgres 镜像首次
# 启动走 initdb 慢路径时会短暂返回 0，紧接着 initdb 完成触发一次 fast-shutdown
# + restart —— 脚本此时往下走就撞上 "the database system is shutting down"
# （现场 19:09 那次失败正是如此）。所以要求连续 PG_READY_STREAK 次查询成功。
PG_READY_STREAK="${PG_READY_STREAK:-3}"

wait_for_pg_ready() {
  local tries="${1:-60}" streak=0
  for _ in $(seq 1 "$tries"); do
    if pg_probe; then
      streak=$((streak + 1))
      (( streak >= PG_READY_STREAK )) && return 0
    else
      streak=0
    fi
    sleep 1
  done
  return 1
}

pg_hba_path() {
  local out=""
  case "$MODE" in
    docker) out="$(docker exec "$PG_CONTAINER" psql -U "$DB_USER" -d postgres -qtAX -c 'SHOW hba_file' 2>/dev/null || true)" ;;
    *)      out="$(as_postgres psql -qtAX -c 'SHOW hba_file' 2>/dev/null || true)" ;;
  esac
  printf '%s' "$out" | tr -d '\r'
}

pg_hba_has_host_trust() {
  local f="$1"
  case "$MODE" in
    docker) docker exec "$PG_CONTAINER" sh -c "grep -E '^[[:space:]]*host[[:space:]]' '$f' | grep -q trust" ;;
    *)      grep -E '^[[:space:]]*host[[:space:]]' "$f" | grep -q trust ;;
  esac
}

tighten_pg_hba() {
  local f="$1"
  case "$MODE" in
    docker)
      docker exec "$PG_CONTAINER" sh -c "cp -n '$f' '$f.trust.bak' 2>/dev/null || true; sed -i -E 's/^([[:space:]]*host[[:space:]].*)trust[[:space:]]*\$/\1scram-sha-256/' '$f'"
      ;;
    *)
      cp -n "$f" "$f.trust.bak" 2>/dev/null || true
      sed -i -E 's/^([[:space:]]*host[[:space:]].*)trust[[:space:]]*$/\1scram-sha-256/' "$f"
      ;;
  esac
}

reload_pg_conf() {
  case "$MODE" in
    docker) docker exec "$PG_CONTAINER" psql -U "$DB_USER" -d postgres -qtAX -c 'SELECT pg_reload_conf()' >/dev/null ;;
    *)      as_postgres psql -qtAX -c 'SELECT pg_reload_conf()' >/dev/null ;;
  esac
}

# 只要 pg_hba 里还有一条 host trust 行，POSTGRES_PASSWORD 就是摆设：现场用错密码
# 也能连上。发现 trust 就收紧成 scram-sha-256 并 reload（改文件本身不会生效，
# 必须 pg_reload_conf()）。
ensure_password_auth() {
  local f
  f="$(pg_hba_path)"
  if [[ -z "$f" ]]; then
    warn "拿不到 pg_hba.conf 路径，跳过密码认证检查"
    return 0
  fi
  if ! pg_hba_has_host_trust "$f"; then
    ok "pg_hba 无 host trust 行，密码认证生效"
    return 0
  fi
  warn "pg_hba.conf 里仍有 host ... trust 行 —— POSTGRES_PASSWORD 目前不生效"
  if (( DRY_RUN )); then
    printf '   [dry-run] 收紧 %s 的 host trust → scram-sha-256 并 pg_reload_conf()\n' "$f"
    return 0
  fi
  tighten_pg_hba "$f"
  reload_pg_conf
  if pg_hba_has_host_trust "$f"; then
    die "pg_hba 收紧失败：$f 仍存在 host trust 行，请人工处理后再跑"
  fi
  ok "已收紧 pg_hba（原文件备份为 $f.trust.bak）并 reload"
}

# ── Redis 辅助：unit 探测 / 安装 / 就绪探测 ────────────────────────────
# Redis 承载 realtime nudge 的跨进程广播，是部署必需组件（应用没有 REDIS_URL
# 启动即失败）。但本脚本的主职责是搬库 —— 所以这里所有失败都只 warn 并返回非 0，
# 由调用方决定怎么提示，绝不 die。
#
# unit 名随发行版不同（Debian/Ubuntu 是 redis-server，RHEL/Fedora 是 redis），
# 所以探测「存在哪一个」，不写死一个名字。加 --no-legend 后不存在的 unit 输出为空，
# 只看输出不看退出码，比单看 list-unit-files 的退出码可靠。
redis_unit_name() {
  local unit out
  for unit in redis-server redis; do
    out="$(systemctl list-unit-files --no-legend "${unit}.service" 2>/dev/null || true)"
    if [[ -n "$out" ]]; then printf '%s' "$unit"; return 0; fi
  done
  return 1
}

# URL 里可能带密码（redis://:PASSWORD@host:6379/0），写日志前抹掉 userinfo。
mask_url() {
  local url="$1"
  if [[ "$url" == *"@"* ]]; then
    printf '%s://***@%s' "${url%%://*}" "${url##*@}"
  else
    printf '%s' "$url"
  fi
}

# 就绪探测连的是配置里那一个 Redis（host/port 从 REDIS_URL 解析），
# 而不是硬编码 127.0.0.1:6379。
redis_url_host() {
  local url="${1#*://}"
  url="${url#*@}"; url="${url%%/*}"; url="${url%%:*}"
  printf '%s' "${url:-127.0.0.1}"
}

redis_url_port() {
  local url="${1#*://}" port
  url="${url#*@}"; url="${url%%/*}"; port="${url##*:}"
  if [[ "$port" =~ ^[0-9]+$ ]]; then printf '%s' "$port"; else printf '6379'; fi
}

# unit 在跑，且（装了 redis-cli 时）真能 ping 通 —— 只看 is-active 会漏掉
# 「unit active 但端口没起来」这种情况。
redis_probe() {
  [[ -n "$REDIS_UNIT" ]] || return 1
  systemctl is-active --quiet "$REDIS_UNIT" 2>/dev/null || return 1
  if command -v redis-cli >/dev/null 2>&1; then
    [[ "$(redis-cli -h "$(redis_url_host "$REDIS_URL_VALUE")" \
                      -p "$(redis_url_port "$REDIS_URL_VALUE")" ping 2>/dev/null || true)" == "PONG" ]]
  fi
}

wait_for_redis_ready() {
  local tries="${1:-15}"
  for _ in $(seq 1 "$tries"); do
    if redis_probe; then return 0; fi
    sleep 1
  done
  return 1
}

# 确保本机 Redis 已装、已 enable --now。返回 0 = 就绪；非 0 只表示「没搞定」，
# 调用方 warn 一下继续搬库。
ensure_redis_systemd() {
  local unit install_failed=0
  if unit="$(redis_unit_name)"; then
    REDIS_UNIT="$unit"
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      ok "Redis 已在运行（$unit），跳过安装"
      return 0
    fi
    log "启用并启动 Redis（$unit）"
    # 失败必须就地返回，不能让 set -e 掀桌子 —— 本脚本的主职责是搬库。
    run systemctl enable --now "$unit" \
      || { warn "systemctl enable --now $unit 失败"; return 1; }
  elif command -v redis-server >/dev/null 2>&1 || command -v redis-cli >/dev/null 2>&1; then
    # 二进制在但没 unit（例如手工编译装的）：不重复装包，也没法 enable --now
    warn "本机有 redis 二进制，但找不到 redis-server/redis 的 systemd unit，无法 enable --now"
    return 1
  else
    if command -v apt-get >/dev/null 2>&1; then
      log "安装 Redis（apt）"
      run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq redis-server \
        || install_failed=1
    elif command -v dnf >/dev/null 2>&1; then
      log "安装 Redis（dnf）"
      run dnf install -y -q redis || install_failed=1
    elif command -v yum >/dev/null 2>&1; then
      log "安装 Redis（yum）"
      run yum install -y -q redis || install_failed=1
    else
      warn "未识别到包管理器，无法自动安装 Redis"
      return 1
    fi
    if (( install_failed )); then
      warn "Redis 安装失败（见上面的包管理器输出）"
      return 1
    fi
    if (( DRY_RUN )); then
      printf '   [dry-run] systemctl enable --now <redis-server|redis>（按装完实际存在的 unit）\n'
    elif unit="$(redis_unit_name)"; then
      REDIS_UNIT="$unit"
      run systemctl enable --now "$unit" \
        || { warn "systemctl enable --now $unit 失败"; return 1; }
    else
      warn "装完仍找不到 redis-server/redis 的 systemd unit，无法 enable --now"
      return 1
    fi
  fi

  if (( DRY_RUN )); then
    printf '   [dry-run] 等 Redis 就绪（systemctl is-active + redis-cli ping）\n'
    return 0
  fi
  if wait_for_redis_ready 15; then return 0; fi
  warn "Redis unit 已 enable，但 15 秒内探活没通过（${REDIS_UNIT:-unit 未知}）"
  return 1
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

  log "启动 PostgreSQL 与 Redis（compose）"
  if (( DRY_RUN )); then
    printf '   [dry-run] docker compose -f %s up -d postgres redis\n' "$COMPOSE_FILE"
  else
    # 两个服务都是必需组件：PG（ADR 0089）与 Redis（ADR 0090，nudge 跨进程广播）。
    # 卷路径是 compose 里的必填项，缺了 compose 直接报错 —— 这里先给更清楚的话。
    for required in POSTGRES_PASSWORD LUYUN_PG_DATA LUYUN_REDIS_DATA; do
      if [[ -z "${!required:-}" ]]; then
        die "启动 postgres/redis 容器需要 $required。通常先 source deploy/.env.docker 再跑本脚本"
      fi
    done
    docker compose -f "$COMPOSE_FILE" up -d postgres redis 2>&1 | tail -3
  fi

  # 等库稳定就绪：连续多次实际查询成功，躲开 initdb 期间的 pg_isready 假阳性
  if (( ! DRY_RUN )); then
    wait_for_pg_ready 90 \
      || die "PostgreSQL 容器未稳定就绪（initdb 可能仍在进行）：docker logs $PG_CONTAINER"
    ok "PostgreSQL 已就绪（连续 ${PG_READY_STREAK} 次查询成功）"
  fi

  # Docker 形态下容器内用服务名互访
  DB_HOST="postgres"
  DB_PASSWORD="${POSTGRES_PASSWORD:-}"
  # Redis 由 compose 的 redis 服务提供（服务名 redis，**没有发布端口**）。容器里的
  # 127.0.0.1 指的是容器自己，所以这个形态的默认 URL 必须是服务名 —— 写前端那个
  # 默认值会把容器配坏。容器首次起来时 docker-entrypoint.sh 也会把空的 REDIS_URL
  # 补成同一个值。已经显式给了 REDIS_URL 就尊重它（指向外部实例的场景）。
  REDIS_URL_VALUE="${REDIS_URL:-redis://redis:6379/0}"
  REDIS_STATUS="ok"
  REDIS_UNIT="compose 服务 redis"
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
    wait_for_pg_ready 40 || die "PostgreSQL 安装后仍不可达（连续 ${PG_READY_STREAK} 次查询未成功）"
    ok "PostgreSQL 已就绪（连续 ${PG_READY_STREAK} 次查询成功）"
  fi
  DB_HOST="${POSTGRES_HOST:-127.0.0.1}"

  # ── Redis（与 PG 同段：本机装 + enable --now）──
  # 装不上/起不来不 die：搬库才是这个脚本的主职责，Redis 的问题在总结里显著提示。
  if ensure_redis_systemd; then
    ok "Redis 已就绪${REDIS_UNIT:+（$REDIS_UNIT）}"
  else
    REDIS_STATUS="fail"
    warn "Redis 未就绪 —— 应用起不来。请手工安装 Redis 并把 REDIS_URL 写进 $ENV_FILE："
    warn "  apt-get:  sudo apt-get install -y redis-server && sudo systemctl enable --now redis-server"
    warn "  dnf/yum:  sudo dnf install -y redis && sudo systemctl enable --now redis"
    warn "  $ENV_FILE 里写：REDIS_URL=$REDIS_URL_DEFAULT"
  fi
fi

# ── 建库 + 建用户 ──────────────────────────────────────────────────────
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

# ── 收紧 pg_hba：trust 会让 POSTGRES_PASSWORD 形同虚设 ────────────────
ensure_password_auth

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
    # 停机已确认，先把 WAL 合并回主库：主库文件自洽后，无论后续用 .backup 还是
    # 直接搬运文件都不会漏掉已提交数据（现场手工 cp 的备份就少了 27 行）。
    run sqlite3 "$SQLITE_DB" "PRAGMA wal_checkpoint(TRUNCATE);"
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

# ── 写 REDIS_URL ───────────────────────────────────────────────────────
# Redis 是部署必需组件：应用没有 REDIS_URL 会启动即失败，所以这一项与搬库结果
# 无关 —— 即使上面没拿到 DSN（要人工补 DSN），URL 也照样写。写法与上面
# DATABASE_BACKEND/POSTGRES_DSN 完全一致：已存在就原地替换，不存在才追加。
if (( DRY_RUN )); then
  printf '   [dry-run] REDIS_URL=%s\n' "$(mask_url "$REDIS_URL_VALUE")"
else
  # 写失败也不 die（搬库已经完成），但必须把状态降级成 fail，总结里显著提示。
  redis_write_ok=1
  if grep -qE '^REDIS_URL=' "$ENV_FILE"; then
    sed -i "s|^REDIS_URL=.*|REDIS_URL=${REDIS_URL_VALUE}|" "$ENV_FILE" || redis_write_ok=0
  else
    printf 'REDIS_URL=%s\n' "$REDIS_URL_VALUE" >> "$ENV_FILE" || redis_write_ok=0
  fi
  if (( redis_write_ok )); then
    chmod 600 "$ENV_FILE" || true
    ok "REDIS_URL 已写入（$(mask_url "$REDIS_URL_VALUE")）"
  else
    REDIS_STATUS="fail"
    warn "写 $ENV_FILE 失败 —— 请手工加一行 REDIS_URL=$REDIS_URL_DEFAULT（应用没有它起不来）"
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
echo "Redis（部署必需，realtime nudge 的跨进程广播）："
case "$REDIS_STATUS" in
  fail)
    warn "Redis 未就绪 —— 应用起不来。"
    echo "    手工安装：sudo apt-get install -y redis-server（RHEL 系：sudo dnf install -y redis）"
    echo "    再 enable --now：sudo systemctl enable --now redis-server   # RHEL 系 unit 名是 redis"
    echo "    然后把 REDIS_URL=$REDIS_URL_DEFAULT 写进 $ENV_FILE 并重启应用。"
    ;;
  *)
    if (( DRY_RUN )); then
      echo "    dry-run：计划如上（未实际执行）；REDIS_URL 将写入 $ENV_FILE。"
    else
      echo "    已就绪${REDIS_UNIT:+（$REDIS_UNIT）}；REDIS_URL 已写入 $ENV_FILE。"
    fi
    ;;
esac
echo
echo "回滚：把 $ENV_FILE 里 DATABASE_BACKEND 改回 sqlite 并重启应用。"
echo "      源库 $SQLITE_DB 全程未被修改。"
echo "      Redis 不参与回滚：REDIS_URL 是必需项，删空它应用反而起不来；不想用本机"
echo "      Redis，就把这一项换成可用实例的地址（或让 compose 提供实例）。"
