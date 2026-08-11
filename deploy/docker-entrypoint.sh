#!/usr/bin/env bash
# Docker process-shell entrypoint (ADR 0011).
# Live app tree is bind-mounted at LUYUN_APP_DIR (default /srv/luyun/app).
# Parent of that path must be the volume mount so Update Job can rename
# app ↔ app.prev / app.next on the same filesystem.
set -euo pipefail

APP_DIR="${LUYUN_APP_DIR:-/srv/luyun/app}"
REPO="${GITHUB_REPO:-YKevin0x61/luyun}"
TAG="${LUYUN_TAG:-latest}"
AUTO_BOOTSTRAP="${LUYUN_DOCKER_AUTO_BOOTSTRAP:-1}"
CONTAINER_NAME="${LUYUN_DOCKER_CONTAINER:-luyun}"

log() { echo "==> $*"; }
die() { echo "错误: $*" >&2; exit 1; }

load_env_file() {
  local env_file="$APP_DIR/deploy/env.production"
  if [[ -f "$env_file" ]]; then
    log "load $env_file"
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
}

github_curl() {
  local token="${GITHUB_RELEASES_TOKEN:-}"
  if [[ -n "$token" ]]; then
    curl -fsSL -H "Authorization: Bearer ${token}" "$@"
  else
    curl -fsSL "$@"
  fi
}

resolve_latest_tag() {
  local json tag
  json="$(
    github_curl \
      -H "Accept: application/vnd.github+json" \
      -H "User-Agent: luyun-docker-entrypoint" \
      "https://api.github.com/repos/${REPO}/releases/latest"
  )" || die "无法查询最新正式 Release（仓库 ${REPO}）"
  tag="$(printf '%s' "$json" | sed -nE 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n 1)"
  [[ -n "$tag" ]] || die "最新正式 Release 响应里没有 tag_name"
  printf '%s' "$tag"
}

file_sha256() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  else
    shasum -a 256 "$path" | awk '{print $1}'
  fi
}

bootstrap_app_tree() {
  local tag="$1"
  local stage bundle sums expected actual
  stage="$(mktemp -d "${TMPDIR:-/tmp}/luyun-docker-bootstrap.XXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -rf '${stage}'" RETURN

  log "download Release Bundle for ${tag}"
  bundle="${stage}/luyun-release-bundle.tar.gz"
  sums="${stage}/SHA256SUMS"
  github_curl \
    -H "User-Agent: luyun-docker-entrypoint" \
    -o "$bundle" \
    "https://github.com/${REPO}/releases/download/${tag}/luyun-release-bundle.tar.gz" \
    || die "下载发行包失败 tag=${tag}"
  github_curl \
    -H "User-Agent: luyun-docker-entrypoint" \
    -o "$sums" \
    "https://github.com/${REPO}/releases/download/${tag}/SHA256SUMS" \
    || die "下载 SHA256SUMS 失败 tag=${tag}"

  expected="$(
    awk '$2 == "luyun-release-bundle.tar.gz" { print $1; found=1; exit }
         END { if (!found) exit 1 }' "$sums"
  )" || die "SHA256SUMS 缺少发行包摘要"
  actual="$(file_sha256 "$bundle")"
  [[ "$actual" == "$expected" ]] || die "checksum mismatch: expected ${expected}, got ${actual}"

  mkdir -p "$APP_DIR"
  # Extract into empty/partial tree; never keep bundle-shipped shop state.
  tar -xzf "$bundle" -C "$APP_DIR"
  rm -rf "$APP_DIR/data" "$APP_DIR/secrets"
  rm -f "$APP_DIR/deploy/env.production"
  [[ -f "$APP_DIR/main.py" ]] || die "发行包解压后缺少 main.py"
  [[ -f "$APP_DIR/RELEASE_MANIFEST.json" ]] || die "发行包解压后缺少 RELEASE_MANIFEST.json"
  log "bootstrapped app tree at ${APP_DIR} (${tag})"
}

ensure_env_production() {
  local env_file="$APP_DIR/deploy/env.production"
  local example="$APP_DIR/deploy/env.production.example"
  mkdir -p "$(dirname "$env_file")"
  if [[ ! -f "$env_file" ]]; then
    if [[ -f "$example" ]]; then
      cp "$example" "$env_file"
    else
      cat > "$env_file" <<EOF
# Seeded by Docker entrypoint — edit remaining values before production use.
GITHUB_REPO=${REPO}
GITHUB_RELEASES_TOKEN=
EOF
    fi
  fi

  # Upsert Docker deploy settings without echoing secrets.
  awk -v repo="$REPO" \
      -v mode="docker" \
      -v container="$CONTAINER_NAME" \
      -v deploy_dir="$APP_DIR" '
    BEGIN { done_repo=0; done_mode=0; done_ctr=0; done_dir=0 }
    /^GITHUB_REPO=/ { print "GITHUB_REPO=" repo; done_repo=1; next }
    /^LUYUN_DEPLOY_MODE=/ { print "LUYUN_DEPLOY_MODE=" mode; done_mode=1; next }
    /^LUYUN_DOCKER_CONTAINER=/ { print "LUYUN_DOCKER_CONTAINER=" container; done_ctr=1; next }
    /^RELEASE_UPDATE_REPO_DIR=/ { print "RELEASE_UPDATE_REPO_DIR=" deploy_dir; done_dir=1; next }
    { print }
    END {
      if (!done_repo) print "GITHUB_REPO=" repo
      if (!done_mode) print "LUYUN_DEPLOY_MODE=" mode
      if (!done_ctr) print "LUYUN_DOCKER_CONTAINER=" container
      if (!done_dir) print "RELEASE_UPDATE_REPO_DIR=" deploy_dir
    }
  ' "$env_file" > "${env_file}.tmp"
  mv "${env_file}.tmp" "$env_file"
  chmod 600 "$env_file" || true
}

ensure_venv() {
  cd "$APP_DIR"
  if [[ ! -x .venv/bin/python ]]; then
    log "create .venv + pip install"
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
  fi
}

ensure_app() {
  mkdir -p "$(dirname "$APP_DIR")"
  if [[ -f "$APP_DIR/main.py" ]]; then
    return 0
  fi
  if [[ "$AUTO_BOOTSTRAP" != "1" ]]; then
    die "应用树缺失（${APP_DIR}/main.py）。请先 Bootstrap 到宿主机 runtime/app，或设 LUYUN_DOCKER_AUTO_BOOTSTRAP=1"
  fi
  local tag="$TAG"
  if [[ -z "$tag" || "$tag" == "latest" ]]; then
    tag="$(resolve_latest_tag)"
    log "latest formal Release → ${tag}"
  fi
  bootstrap_app_tree "$tag"
}

cmd="${1:-serve}"
shift || true

case "$cmd" in
  serve)
    ensure_app
    ensure_env_production
    load_env_file
    # Re-assert after sourcing shop env (compose may also inject these).
    export LUYUN_DEPLOY_MODE=docker
    export LUYUN_DOCKER_CONTAINER="${LUYUN_DOCKER_CONTAINER:-$CONTAINER_NAME}"
    export RELEASE_UPDATE_REPO_DIR="${RELEASE_UPDATE_REPO_DIR:-$APP_DIR}"
    export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/ms-playwright}"
    ensure_venv
    log "uvicorn main:app (workers=1) cwd=${APP_DIR}"
    cd "$APP_DIR"
    exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
    ;;
  bootstrap)
    # One-shot: download/extract bundle only (no serve).
    ensure_app
    ensure_env_production
    log "bootstrap complete: ${APP_DIR}"
    ;;
  *)
    # Allow `docker compose run luyun bash` etc.
    exec "$cmd" "$@"
    ;;
esac
