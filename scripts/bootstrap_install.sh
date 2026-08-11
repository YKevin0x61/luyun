#!/usr/bin/env bash
# Bootstrap Install: new machine → app process can start (ADR 0011).
# Downloads and verifies the same Release Bundle used by Apply Update.
# Shop machines stay Node-free; no Deploy Key / git clone.
set -euo pipefail

usage() {
  cat <<'EOF'
用法: scripts/bootstrap_install.sh [选项]

新机器引导安装到「应用进程可启动」：用 Releases 只读 PAT 下载并硬校验
发行包（luyun-release-bundle.tar.gz + SHA256SUMS）、解压、Python/Playwright
依赖、写入版本清单身份、启用 systemd（luyun + luyun-update）。
不装 Node；不 clone；不需要 Deploy Key；不含反代/TLS/POS 凭据。

一键（curl|bash）请用 Release 资产 install.sh / scripts/curl_install.sh，
见 docs/RELEASE_AND_DEPLOY.md。

选项:
  -h, --help                 显示此帮助
  --dry-run                  只校验参数并打印计划（不改系统）
  --repo owner/name          GitHub 仓库（或环境变量 GITHUB_REPO）
  --tag TAG                  要安装的正式 Release tag
  --deploy-dir DIR           部署目录（默认 /opt/luyun）
  --releases-token TOKEN     Releases API 只读 fine-grained PAT
                             （或环境变量 GITHUB_RELEASES_TOKEN）

缺省凭据时：若 stdin 是 TTY，会提示输入 PAT；
非交互环境必须用 --releases-token（或环境变量）。

环境变量:
  GITHUB_REPO / GITHUB_RELEASES_TOKEN
  LUYUN_BOOTSTRAP_SKIP_PLAYWRIGHT_DEPS=1     跳过 playwright --with-deps（测试用）
  LUYUN_BOOTSTRAP_SKIP_SYSTEMD_ROOT_CHECK=1  测试用：跳过非 root 装单元检查

凭据落盘（供 Update Job / Version Check 复用，mode 600，不入 git）:
  <deploy-dir>/deploy/env.production   （含 GITHUB_REPO / GITHUB_RELEASES_TOKEN）
EOF
}

die() { echo "错误: $*" >&2; exit 1; }
log() { echo "==> $*"; }

DRY_RUN=0
REPO="${GITHUB_REPO:-}"
TAG=""
DEPLOY_DIR="${LUYUN_BOOTSTRAP_DEPLOY_DIR:-/opt/luyun}"
RELEASES_TOKEN="${GITHUB_RELEASES_TOKEN:-}"

BUNDLE_ASSET="luyun-release-bundle.tar.gz"
CHECKSUMS_ASSET="SHA256SUMS"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --repo)
      [[ $# -ge 2 ]] || die "--repo 需要参数"
      REPO="$2"; shift 2 ;;
    --tag)
      [[ $# -ge 2 ]] || die "--tag 需要参数"
      TAG="$2"; shift 2 ;;
    --deploy-dir)
      [[ $# -ge 2 ]] || die "--deploy-dir 需要参数"
      DEPLOY_DIR="$2"; shift 2 ;;
    --releases-token)
      [[ $# -ge 2 ]] || die "--releases-token 需要参数"
      RELEASES_TOKEN="$2"; shift 2 ;;
    --deploy-key-file|--git-url)
      die "已废弃选项 $1：Bootstrap 使用 Release Bundle + Releases PAT，不再需要 Deploy Key / git clone" ;;
    -*) die "未知选项: $1" ;;
    *) die "多余参数: $1" ;;
  esac
done

[[ -n "$REPO" ]] || die "缺少 --repo / GITHUB_REPO（格式 owner/name）"
[[ -n "$TAG" ]] || die "缺少 --tag（要安装的 Release tag）"
[[ "$REPO" == */* ]] || die "GITHUB_REPO 格式应为 owner/name，收到: $REPO"

ENV_FILE="$DEPLOY_DIR/deploy/env.production"

_can_prompt_tty() {
  # curl|bash consumes stdin; still allow interactive paste via the real terminal.
  # Mere -r/-w on /dev/tty can pass in CI without a controlling tty — must open it.
  { exec 3<>/dev/tty; } 2>/dev/null || return 1
  exec 3>&-
  return 0
}

resolve_credentials() {
  if [[ -z "$RELEASES_TOKEN" ]]; then
    if [[ -t 0 ]]; then
      echo -n "请输入 Releases API 只读 PAT: " >&2
      read -r -s RELEASES_TOKEN
      echo >&2
      [[ -n "$RELEASES_TOKEN" ]] || die "未收到 Releases PAT"
    elif _can_prompt_tty; then
      echo -n "请输入 Releases API 只读 PAT: " >/dev/tty
      read -r -s RELEASES_TOKEN </dev/tty
      echo >/dev/tty
      [[ -n "$RELEASES_TOKEN" ]] || die "未收到 Releases PAT"
    else
      die "缺少 GitHub 凭据：需要 --releases-token / GITHUB_RELEASES_TOKEN（Releases 只读 PAT）"
    fi
  fi
}

resolve_credentials

file_sha256() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  else
    die "需要 sha256sum 或 shasum（用于发行包硬校验）"
  fi
}

print_contract() {
  # Use ${VAR} before non-ASCII so bash does not eat UTF-8 lead bytes into names
  # under C/POSIX locales (set -u would then trip on a bogus variable).
  cat <<EOF
BOOTSTRAP_CONTRACT
deploy_dir=${DEPLOY_DIR}
repo=${REPO}
tag=${TAG}
env_file=${ENV_FILE}
bundle_asset=${BUNDLE_ASSET}
checksums_asset=${CHECKSUMS_ASSET}
plan:
  - download Release Bundle ${BUNDLE_ASSET} + ${CHECKSUMS_ASSET}
  - hard-verify checksum (fail closed on mismatch/missing)
  - extract bundle into deploy dir (prebuilt Admin/KDS + RELEASE_MANIFEST.json)
  - write mode-restricted PAT secrets in env.production
  - python3 -m venv .venv && pip install -r requirements.txt
  - playwright install chromium (no Node; once at bootstrap)
  - Release Manifest remains installed identity
  - install+enable systemd units: luyun.service + luyun-update.service
manual_followups:
  - edit ${ENV_FILE} (fill LUYUN_CRED_KEY etc; keep GITHUB_*)
  - configure reverse proxy + TLS (deploy/Caddyfile or deploy/nginx.conf)
  - enter POS credentials via /setup
EOF
}

print_manual_followups() {
  cat <<EOF

==> Bootstrap finished (units enabled / startable). Manual follow-ups:
  1. Edit env file: ${ENV_FILE}
     (fill LUYUN_CRED_KEY etc; GITHUB_REPO / GITHUB_RELEASES_TOKEN already set)
  2. Configure reverse proxy + TLS (Caddy/Nginx; see deploy/Caddyfile or deploy/nginx.conf)
  3. Start main service: sudo systemctl start luyun.service
  4. Enter POS credentials in Admin /setup
  (If Bootstrap was not run as root: create user luyun, install staged units from
   ${DEPLOY_DIR}/deploy/systemd-staged/ into /etc/systemd/system/, then enable.)

Secrets (mode 600, do not commit):
  env file:   ${ENV_FILE}
EOF
}

require_tools() {
  local missing=0
  for cmd in python3 tar curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "错误: 缺少必需工具: $cmd" >&2
      missing=1
    fi
  done
  if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    echo "错误: 缺少必需工具: sha256sum 或 shasum（发行包硬校验）" >&2
    missing=1
  fi
  if [[ "$DRY_RUN" -eq 0 ]]; then
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "错误: 缺少必需工具: systemctl（需要 systemd 以安装服务单元）" >&2
      missing=1
    fi
  fi
  [[ "$missing" -eq 0 ]] || die "请先安装缺失工具后再运行 Bootstrap Install"
}

write_env_secrets() {
  # Prefer the extracted example; keep an existing env.production if already edited.
  mkdir -p "$(dirname "$ENV_FILE")"
  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$DEPLOY_DIR/deploy/env.production.example" ]]; then
      /bin/cp "$DEPLOY_DIR/deploy/env.production.example" "$ENV_FILE"
    else
      cat > "$ENV_FILE" <<EOF
# Seeded by Bootstrap Install — edit remaining values before starting the app.
GITHUB_REPO=
GITHUB_RELEASES_TOKEN=
EOF
    fi
  fi

  # Upsert GitHub settings without echoing the token to logs.
  # Drop legacy GIT_SSH_COMMAND — Bootstrap/Update Job no longer use Deploy Key.
  awk -v repo="$REPO" -v token="$RELEASES_TOKEN" '
    BEGIN { done_repo=0; done_tok=0 }
    /^GITHUB_REPO=/ { print "GITHUB_REPO=" repo; done_repo=1; next }
    /^GITHUB_RELEASES_TOKEN=/ { print "GITHUB_RELEASES_TOKEN=" token; done_tok=1; next }
    /^GIT_SSH_COMMAND=/ { next }
    { print }
    END {
      if (!done_repo) print "GITHUB_REPO=" repo
      if (!done_tok) print "GITHUB_RELEASES_TOKEN=" token
    }
  ' "$ENV_FILE" > "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

download_release_asset() {
  local tag="$1" name="$2" dest="$3"
  local url="https://github.com/${REPO}/releases/download/${tag}/${name}"
  curl -fsSL \
    -H "Authorization: Bearer ${RELEASES_TOKEN}" \
    -H "Accept: application/octet-stream" \
    -H "User-Agent: luyun-bootstrap" \
    -o "$dest" \
    "$url" \
    || return 1
}

verify_bundle_checksum() {
  local bundle_path="$1" sums_path="$2"
  [[ -f "$sums_path" ]] || die "missing integrity file ${CHECKSUMS_ASSET}"
  local expected actual
  expected="$(
    awk -v name="$BUNDLE_ASSET" '
      $2 == name { print $1; found=1; exit }
      END { if (!found) exit 1 }
    ' "$sums_path"
  )" || die "missing digest for ${BUNDLE_ASSET} in ${CHECKSUMS_ASSET}"
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die "invalid digest in ${CHECKSUMS_ASSET}"
  actual="$(file_sha256 "$bundle_path")"
  if [[ "$actual" != "$expected" ]]; then
    die "checksum mismatch for ${BUNDLE_ASSET}: expected ${expected}, got ${actual}"
  fi
  log "verified ${BUNDLE_ASSET} checksum"
}

assert_bundle_tree() {
  local root="$1"
  [[ -f "${root}/RELEASE_MANIFEST.json" ]] || die "missing RELEASE_MANIFEST.json after extract"
  [[ -f "${root}/admin-web/dist/index.html" ]] || die "missing admin-web/dist/index.html after extract"
  [[ -f "${root}/public/kds/index.html" ]] || die "missing public/kds/index.html after extract"
  [[ -d "${root}/public/kds/assets" ]] || die "missing public/kds/assets/ after extract"
  [[ -f "${root}/requirements.txt" ]] || die "missing requirements.txt after extract"
}

prepare_deploy_dir() {
  if [[ -e "$DEPLOY_DIR" ]] && [[ -n "$(ls -A "$DEPLOY_DIR" 2>/dev/null || true)" ]]; then
    die "deploy dir is not empty: ${DEPLOY_DIR} (Bootstrap expects a fresh directory)"
  fi
  # Leave DEPLOY_DIR absent until the verified tree is ready to move into place.
  if [[ -d "$DEPLOY_DIR" ]]; then
    rmdir "$DEPLOY_DIR" || die "deploy dir exists and is not a removable empty directory: ${DEPLOY_DIR}"
  fi
  mkdir -p "$(dirname "$DEPLOY_DIR")"
}

install_release_bundle() {
  log "download Release Bundle for ${TAG}"
  local stage tree
  stage="$(mktemp -d "${TMPDIR:-/tmp}/luyun-bootstrap-bundle.XXXXXX")"
  tree="${stage}/tree"
  # shellcheck disable=SC2064
  trap "rm -rf '${stage}'" RETURN

  download_release_asset "$TAG" "$BUNDLE_ASSET" "${stage}/${BUNDLE_ASSET}" \
    || die "failed to download Release asset ${BUNDLE_ASSET} for tag ${TAG}"
  download_release_asset "$TAG" "$CHECKSUMS_ASSET" "${stage}/${CHECKSUMS_ASSET}" \
    || die "failed to download Release asset ${CHECKSUMS_ASSET} for tag ${TAG}"

  verify_bundle_checksum "${stage}/${BUNDLE_ASSET}" "${stage}/${CHECKSUMS_ASSET}"

  log "extract ${BUNDLE_ASSET} (side tree; activate only after verify)"
  mkdir -p "$tree"
  tar -xzf "${stage}/${BUNDLE_ASSET}" -C "$tree"
  # Never keep bundle-shipped shop state even if a bad archive contains it.
  rm -rf "$tree/data" "$tree/secrets"
  rm -f "$tree/deploy/env.production"
  assert_bundle_tree "$tree"

  if [[ -e "$DEPLOY_DIR" ]]; then
    die "deploy dir appeared during install: ${DEPLOY_DIR}"
  fi
  mv "$tree" "$DEPLOY_DIR"
  trap - RETURN
  rm -rf "$stage"
}

install_python_stack() {
  log "create venv + install Python deps"
  (
    cd "$DEPLOY_DIR"
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
  )
  log "install Playwright Chromium"
  if [[ "${LUYUN_BOOTSTRAP_SKIP_PLAYWRIGHT_DEPS:-}" == "1" ]]; then
    "$DEPLOY_DIR/.venv/bin/python" -m playwright install chromium
  else
    "$DEPLOY_DIR/.venv/bin/python" -m playwright install --with-deps chromium
  fi
}

ensure_runtime_user() {
  # Units run as User=luyun; create the account when we have root.
  if [[ "$(id -u)" -ne 0 ]]; then
    return 0
  fi
  if ! id -u luyun >/dev/null 2>&1; then
    log "create system user luyun"
    useradd --system --create-home --shell /usr/sbin/nologin luyun \
      || die "failed to create system user luyun"
  fi
  chown -R luyun:luyun "$DEPLOY_DIR" \
    || die "failed to chown ${DEPLOY_DIR} to luyun"
}

install_systemd_units() {
  log "install systemd units (luyun + luyun-update)"
  local unit_src_main unit_src_update
  unit_src_main="$DEPLOY_DIR/deploy/luyun.service"
  unit_src_update="$DEPLOY_DIR/deploy/luyun-update.service"
  [[ -f "$unit_src_main" ]] || die "missing ${unit_src_main}"
  [[ -f "$unit_src_update" ]] || die "missing ${unit_src_update}"

  local staged
  staged="$(mktemp -d "${TMPDIR:-/tmp}/luyun-units.XXXXXX")"
  sed "s|/opt/luyun|${DEPLOY_DIR}|g" "$unit_src_main" > "$staged/luyun.service"
  sed "s|/opt/luyun|${DEPLOY_DIR}|g" "$unit_src_update" > "$staged/luyun-update.service"

  mkdir -p "$DEPLOY_DIR/deploy/systemd-staged"
  /bin/cp "$staged/luyun.service" "$DEPLOY_DIR/deploy/systemd-staged/luyun.service"
  /bin/cp "$staged/luyun-update.service" "$DEPLOY_DIR/deploy/systemd-staged/luyun-update.service"

  local staged_dir="${DEPLOY_DIR}/deploy/systemd-staged"
  if [[ "$(id -u)" -eq 0 ]]; then
    /bin/cp "$staged/luyun.service" /etc/systemd/system/luyun.service
    /bin/cp "$staged/luyun-update.service" /etc/systemd/system/luyun-update.service
  elif [[ "${LUYUN_BOOTSTRAP_SKIP_SYSTEMD_ROOT_CHECK:-}" != "1" ]] \
    && { [[ ! -f /etc/systemd/system/luyun.service ]] \
      || [[ ! -f /etc/systemd/system/luyun-update.service ]]; }; then
    rm -rf "$staged"
    cat >&2 <<EOF
错误: 非 root 无法把单元装进 /etc/systemd/system（代码/依赖/发行包已就绪）。
请用 sudo 完成启用：
  sudo cp ${staged_dir}/luyun.service /etc/systemd/system/luyun.service
  sudo cp ${staged_dir}/luyun-update.service /etc/systemd/system/luyun-update.service
  sudo systemctl daemon-reload
  sudo systemctl enable luyun.service luyun-update.service
或重新以 root/sudo 运行 Bootstrap Install。
EOF
    die "systemd units not enabled (need root)"
  fi

  systemctl daemon-reload || true
  systemctl enable luyun.service \
    || die "failed to enable luyun.service"
  systemctl enable luyun-update.service \
    || die "failed to enable luyun-update.service"
  rm -rf "$staged"
}

# --- main ---
require_tools
print_contract

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run: checks passed; no changes made"
  print_manual_followups
  exit 0
fi

prepare_deploy_dir
install_release_bundle
write_env_secrets
install_python_stack
ensure_runtime_user
install_systemd_units
print_manual_followups
log "Bootstrap Install finished"
