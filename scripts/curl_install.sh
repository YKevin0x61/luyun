#!/usr/bin/env bash
# One-click Bootstrap Install entry for: curl … | bash
# Fetches scripts/bootstrap_install.sh for the target Release tag, then execs it.
#
# 最短用法（默认装「最新正式 Release」；装机只需 Releases 只读 PAT）:
#   export GITHUB_RELEASES_TOKEN=ghp_xxx
#   curl -fsSL -H "Authorization: Bearer $GITHUB_RELEASES_TOKEN" \
#     -L https://github.com/OWNER/REPO/releases/latest/download/install.sh \
#     | sudo -E bash
#
# sudo -E 用来把上面的环境变量传给 root 下的 bash。
# 定点版本：把 URL 里的 latest 换成 tag，或设 LUYUN_TAG=vX.Y.Z。
set -euo pipefail

# Filled by publish_release.sh when attaching install.sh to a Release.
# Leave empty in the repo source; empty/latest TAG → resolve via Releases API.
LUYUN_EMBEDDED_REPO=""
LUYUN_EMBEDDED_TAG=""

usage() {
  cat <<'EOF'
用法: curl …/releases/latest/download/install.sh | sudo -E bash
      curl_install.sh [选项]   # 高级：显式传参

最短装机（推荐，默认最新正式版）:
  export GITHUB_RELEASES_TOKEN=ghp_xxx
  curl -fsSL -H "Authorization: Bearer $GITHUB_RELEASES_TOKEN" \
    -L https://github.com/<owner>/<repo>/releases/latest/download/install.sh \
    | sudo -E bash

环境变量:
  GITHUB_RELEASES_TOKEN      Releases 只读 PAT（必需）
  LUYUN_DEPLOY_DIR           部署目录（默认 /opt/luyun）
  GITHUB_REPO                覆盖脚本内嵌的 repo
  LUYUN_TAG                  覆盖 tag；空或 latest = 解析最新正式 Release
  LUYUN_BOOTSTRAP_SCRIPT     本地 bootstrap 路径（测试用）

选项（高级，与 bootstrap_install.sh 对齐）:
  -h, --help / --dry-run / --repo / --tag / --deploy-dir / --releases-token

详见 docs/RELEASE_AND_DEPLOY.md
EOF
}

die() { echo "错误: $*" >&2; exit 1; }
log() { echo "==> $*" >&2; }

REPO="${GITHUB_REPO:-${LUYUN_EMBEDDED_REPO:-}}"
# Prefer explicit LUYUN_TAG; else embedded; else "latest" (resolve via API).
TAG="${LUYUN_TAG:-${LUYUN_EMBEDDED_TAG:-latest}}"
RELEASES_TOKEN="${GITHUB_RELEASES_TOKEN:-}"
DEPLOY_DIR="${LUYUN_DEPLOY_DIR:-}"
USER_ARGS=("$@")
HAVE_USER_ARGS=0
[[ $# -gt 0 ]] && HAVE_USER_ARGS=1

i=0
while [[ $i -lt $# ]]; do
  i=$((i + 1))
  arg="${!i}"
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --repo)
      i=$((i + 1))
      [[ $i -le $# ]] || die "--repo 需要参数"
      REPO="${!i}"
      ;;
    --tag)
      i=$((i + 1))
      [[ $i -le $# ]] || die "--tag 需要参数"
      TAG="${!i}"
      ;;
    --releases-token)
      i=$((i + 1))
      [[ $i -le $# ]] || die "--releases-token 需要参数"
      RELEASES_TOKEN="${!i}"
      ;;
    --deploy-dir)
      i=$((i + 1))
      [[ $i -le $# ]] || die "--deploy-dir 需要参数"
      DEPLOY_DIR="${!i}"
      ;;
    --deploy-key-file|--git-url)
      die "已废弃选项 ${arg}：Bootstrap 使用 Release Bundle + Releases PAT，不再需要 Deploy Key / git clone"
      ;;
  esac
done

[[ -n "$REPO" ]] || die "缺少仓库名：请用带内嵌 REPO 的 Release install.sh，或设 GITHUB_REPO / --repo"
[[ "$REPO" == */* ]] || die "GITHUB_REPO 格式应为 owner/name，收到: $REPO"
[[ -n "$RELEASES_TOKEN" ]] || die "缺少 GITHUB_RELEASES_TOKEN（curl 私有 Release 与下载 bootstrap 都需要）。若用了 sudo，请加 -E：sudo -E bash"

command -v curl >/dev/null 2>&1 || die "需要 curl"
command -v bash >/dev/null 2>&1 || die "需要 bash"

resolve_latest_tag() {
  # GitHub "latest" = newest non-prerelease, non-draft Release.
  local api_url json tag
  api_url="https://api.github.com/repos/${REPO}/releases/latest"
  json="$(
    curl -fsSL \
      -H "Authorization: Bearer ${RELEASES_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      -H "User-Agent: luyun-curl-install" \
      "$api_url"
  )" || die "无法查询最新正式 Release（检查 PAT 与仓库 ${REPO}）"
  tag="$(printf '%s' "$json" | sed -nE 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n 1)"
  [[ -n "$tag" ]] || die "最新正式 Release 响应里没有 tag_name"
  printf '%s' "$tag"
}

if [[ -z "$TAG" || "$TAG" == "latest" ]]; then
  log "resolve latest formal Release tag for ${REPO}"
  TAG="$(resolve_latest_tag)"
  log "latest tag → ${TAG}"
fi

BOOTSTRAP_TMP=""
cleanup() {
  if [[ -n "${BOOTSTRAP_TMP:-}" && -f "${BOOTSTRAP_TMP}" ]]; then
    rm -f "${BOOTSTRAP_TMP}"
  fi
}
trap cleanup EXIT

download_bootstrap() {
  local ref="${LUYUN_CURL_INSTALL_REF:-$TAG}"
  local url="https://api.github.com/repos/${REPO}/contents/scripts/bootstrap_install.sh?ref=${ref}"
  BOOTSTRAP_TMP="$(mktemp "${TMPDIR:-/tmp}/luyun-bootstrap.XXXXXX.sh")"
  log "download bootstrap_install.sh from ${REPO}@${ref}"
  curl -fsSL \
    -H "Authorization: Bearer ${RELEASES_TOKEN}" \
    -H "Accept: application/vnd.github.raw" \
    -H "User-Agent: luyun-curl-install" \
    -o "${BOOTSTRAP_TMP}" \
    "$url" \
    || die "无法下载 bootstrap_install.sh（检查 PAT 权限与 tag/ref: ${ref}）"
  chmod 700 "${BOOTSTRAP_TMP}"
  if ! grep -q 'Bootstrap Install' "${BOOTSTRAP_TMP}" 2>/dev/null; then
    die "下载内容不像 bootstrap_install.sh（ref=${ref}）"
  fi
}

if [[ -n "${LUYUN_BOOTSTRAP_SCRIPT:-}" ]]; then
  [[ -f "$LUYUN_BOOTSTRAP_SCRIPT" ]] || die "LUYUN_BOOTSTRAP_SCRIPT 不存在: $LUYUN_BOOTSTRAP_SCRIPT"
  log "using local bootstrap: $LUYUN_BOOTSTRAP_SCRIPT"
  BOOTSTRAP_PATH="$LUYUN_BOOTSTRAP_SCRIPT"
  trap - EXIT
else
  download_bootstrap
  BOOTSTRAP_PATH="$BOOTSTRAP_TMP"
fi

export GITHUB_REPO="$REPO"
export GITHUB_RELEASES_TOKEN="$RELEASES_TOKEN"

# Build bootstrap argv. TAG is always a concrete release tag here (never "latest").
build_bootstrap_args() {
  BOOTSTRAP_ARGS=(--repo "$REPO" --tag "$TAG" --releases-token "$RELEASES_TOKEN")
  if [[ -n "$DEPLOY_DIR" ]]; then
    BOOTSTRAP_ARGS+=(--deploy-dir "$DEPLOY_DIR")
  fi
  # Preserve advanced flags from the caller (--dry-run).
  local i=0
  while [[ $i -lt ${#USER_ARGS[@]} ]]; do
    local a="${USER_ARGS[$i]}"
    case "$a" in
      --dry-run)
        BOOTSTRAP_ARGS+=(--dry-run)
        i=$((i + 1))
        ;;
      --repo|--tag|--releases-token|--deploy-dir)
        # Already applied from resolved env / flags.
        i=$((i + 2))
        ;;
      --deploy-key-file|--git-url)
        die "已废弃选项 ${a}：Bootstrap 使用 Release Bundle + Releases PAT，不再需要 Deploy Key / git clone"
        ;;
      -h|--help)
        i=$((i + 1))
        ;;
      *)
        i=$((i + 1))
        ;;
    esac
  done
}

build_bootstrap_args
log "exec bootstrap_install.sh ${BOOTSTRAP_ARGS[*]}"
exec bash "$BOOTSTRAP_PATH" "${BOOTSTRAP_ARGS[@]}"
