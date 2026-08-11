#!/usr/bin/env bash
# Publish a GitHub Release whose shop-facing artifact is one Release Bundle
# (app tree + prebuilt Admin/KDS + Release Manifest) plus hard checksums.
# Runtime Instances never build frontend on the shop machine.
set -euo pipefail

ROOT="${LUYUN_PUBLISH_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

usage() {
  cat <<'EOF'
用法: scripts/publish_release.sh [--dry-run] <tag>

为指定 tag 发布 GitHub Release，并附带单一发行包与校验材料。

选项:
  -h, --help   显示此帮助
  --dry-run    只校验并打印计划（不构建、不打 tag、不调用 gh）

参数:
  tag          发行版 tag，须与 config.py 中 APP_VERSION 对齐
               （tag 可带或不带前导 v；APP_VERSION 为裸 semver，如 0.1.0）

环境变量:
  LUYUN_PUBLISH_ROOT  覆盖仓库根目录（测试用）

Release 资产布局（Update Job / Bootstrap 契约）:
  luyun-release-bundle.tar.gz  → 应用树 + 预构建 Admin/KDS + RELEASE_MANIFEST.json
  SHA256SUMS                   → 发行包硬校验 sidecar
  install.sh                   → curl|bash 一键引导入口（同 scripts/curl_install.sh）

详见 docs/release-asset-layout.md / docs/RELEASE_AND_DEPLOY.md
EOF
}

die() { echo "错误: $*" >&2; exit 1; }
log() { echo "==> $*"; }

DRY_RUN=0
TAG=""
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    -*) die "未知选项: $arg" ;;
    *)
      if [[ -n "$TAG" ]]; then
        die "多余参数: $arg"
      fi
      TAG="$arg"
      ;;
  esac
done

[[ -n "$TAG" ]] || { usage >&2; die "缺少 tag 参数"; }

require_clean_worktree() {
  local status
  status="$(git -C "$ROOT" status --porcelain)"
  if [[ -n "$status" ]]; then
    die "工作区不干净（dirty worktree），请先提交或清理后再发布"
  fi
}

read_app_version() {
  local config="$ROOT/config.py"
  [[ -f "$config" ]] || die "找不到 config.py: $config"
  local version
  version="$(
    sed -nE 's/^[[:space:]]*APP_VERSION:[[:space:]]*str[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$config" \
      | head -n 1
  )"
  [[ -n "$version" ]] || die "无法从 config.py 解析 APP_VERSION"
  printf '%s' "$version"
}

normalize_tag_version() {
  # Strip a single leading v/V so tag v0.1.0 aligns with APP_VERSION 0.1.0.
  local raw="$1"
  if [[ "$raw" =~ ^[vV](.+)$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  else
    printf '%s' "$raw"
  fi
}

require_version_aligned() {
  local app_version tag_version
  app_version="$(read_app_version)"
  tag_version="$(normalize_tag_version "$TAG")"
  if [[ "$app_version" != "$tag_version" ]]; then
    die "APP_VERSION ($app_version) 与 tag ($TAG) 不一致；请先对齐后再发布"
  fi
}

BUNDLE_ASSET_NAME="luyun-release-bundle.tar.gz"
CHECKSUMS_ASSET_NAME="SHA256SUMS"
INSTALL_ASSET_NAME="install.sh"
MANIFEST_NAME="RELEASE_MANIFEST.json"
ADMIN_DIST_PATH="admin-web/dist"
KDS_DIST_PATH="public/kds"

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  else
    die "需要 sha256sum 或 shasum（用于发行包硬校验）"
  fi
}

write_sha256sums() {
  local file_path="$1"
  local out_path="$2"
  local digest name
  digest="$(sha256_file "$file_path")"
  name="$(basename "$file_path")"
  # GNU coreutils / macOS shasum compatible two-space form.
  printf '%s  %s\n' "$digest" "$name" > "$out_path"
}

print_plan() {
  cat <<EOF
==> plan: build Admin SPA (cd admin-web && npm ci && npm run build)
==> plan: build KDS (./scripts/build_kds.sh)
==> plan: package $BUNDLE_ASSET_NAME (app tree + $ADMIN_DIST_PATH + $KDS_DIST_PATH + $MANIFEST_NAME)
==> plan: exclude shop data/, secrets/, credentials, Playwright browsers from bundle
==> plan: write $CHECKSUMS_ASSET_NAME for hard verify-before-activate
==> plan: attach $INSTALL_ASSET_NAME (curl|bash Bootstrap entry)
==> plan: git tag $TAG at HEAD (refuse if tag exists elsewhere)
==> plan: git push origin HEAD && git push origin refs/tags/$TAG
==> plan: gh release create $TAG --target HEAD $BUNDLE_ASSET_NAME $CHECKSUMS_ASSET_NAME $INSTALL_ASSET_NAME
EOF
}

require_clean_worktree
require_version_aligned

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run: checks passed for tag $TAG (worktree clean, APP_VERSION aligned)"
  print_plan
  exit 0
fi

command -v npm >/dev/null 2>&1 || die "需要 npm（用于构建 Admin SPA）"
command -v gh >/dev/null 2>&1 || die "需要 gh（GitHub CLI，用于创建 Release）"
command -v tar >/dev/null 2>&1 || die "需要 tar（用于打包发行包）"
command -v git >/dev/null 2>&1 || die "需要 git"
[[ -x "$ROOT/scripts/build_kds.sh" ]] || die "缺少可执行的 scripts/build_kds.sh"
[[ -f "$ROOT/requirements.txt" ]] || die "缺少 requirements.txt（用于 requirements fingerprint）"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/luyun-publish.XXXXXX")"
BUNDLE_ROOT="$STAGE/bundle_root"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

log "build Admin SPA"
(
  cd "$ROOT/admin-web"
  npm ci
  npm run build
)
[[ -f "$ROOT/$ADMIN_DIST_PATH/index.html" ]] || die "Admin SPA 构建失败：缺少 $ADMIN_DIST_PATH/index.html"

log "build KDS"
"$ROOT/scripts/build_kds.sh"
[[ -f "$ROOT/$KDS_DIST_PATH/index.html" ]] || die "KDS 构建失败：缺少 $KDS_DIST_PATH/index.html"
[[ -d "$ROOT/$KDS_DIST_PATH/assets" ]] || die "KDS 构建失败：缺少 $KDS_DIST_PATH/assets/"

log "stage Release Bundle tree → $BUNDLE_ROOT"
mkdir -p "$BUNDLE_ROOT"
# Tracked application tree only (never data/, secrets/, node_modules, browsers).
git -C "$ROOT" archive --format=tar HEAD | tar -C "$BUNDLE_ROOT" -xf -

# Overlay prebuilt frontends (gitignored build outputs).
mkdir -p "$BUNDLE_ROOT/$ADMIN_DIST_PATH" "$BUNDLE_ROOT/$KDS_DIST_PATH"
cp -R "$ROOT/$ADMIN_DIST_PATH/." "$BUNDLE_ROOT/$ADMIN_DIST_PATH/"
cp -R "$ROOT/$KDS_DIST_PATH/." "$BUNDLE_ROOT/$KDS_DIST_PATH/"

# Defense in depth: never ship writable shop state, credentials, or browsers.
rm -rf \
  "$BUNDLE_ROOT/data" \
  "$BUNDLE_ROOT/secrets" \
  "$BUNDLE_ROOT/logs" \
  "$BUNDLE_ROOT/ms-playwright" \
  "$BUNDLE_ROOT/.playwright-mcp" \
  "$BUNDLE_ROOT/admin-web/node_modules" \
  "$BUNDLE_ROOT/kds/node_modules" \
  "$BUNDLE_ROOT/kds/unpackage" \
  "$BUNDLE_ROOT/.venv" \
  "$BUNDLE_ROOT/venv"
rm -f \
  "$BUNDLE_ROOT/.env" \
  "$BUNDLE_ROOT/deploy/env.production" \
  "$BUNDLE_ROOT/data/credentials.enc" \
  "$BUNDLE_ROOT/data/.cred_key"

HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
APP_VERSION="$(read_app_version)"
REQ_FP="sha256:$(sha256_file "$ROOT/requirements.txt")"

log "write $MANIFEST_NAME"
python3 - "$BUNDLE_ROOT/$MANIFEST_NAME" "$TAG" "$APP_VERSION" "$HEAD_SHA" "$REQ_FP" \
  "$BUNDLE_ASSET_NAME" "$CHECKSUMS_ASSET_NAME" <<'PY'
import json
import sys

out, tag, app_version, commit, req_fp, bundle, checksums = sys.argv[1:8]
payload = {
    "schema_version": 1,
    "tag": tag,
    "app_version": app_version,
    "commit": commit,
    "requirements_fingerprint": req_fp,
    "artifacts": {
        "bundle": bundle,
        "checksums": checksums,
    },
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY

[[ -f "$BUNDLE_ROOT/$ADMIN_DIST_PATH/index.html" ]] || die "发行包缺少 $ADMIN_DIST_PATH/index.html"
[[ -f "$BUNDLE_ROOT/$KDS_DIST_PATH/index.html" ]] || die "发行包缺少 $KDS_DIST_PATH/index.html"
[[ -f "$BUNDLE_ROOT/$MANIFEST_NAME" ]] || die "发行包缺少 $MANIFEST_NAME"

log "package $BUNDLE_ASSET_NAME"
# Avoid macOS AppleDouble (._*) noise inside the shop-facing archive.
COPYFILE_DISABLE=1 tar -C "$BUNDLE_ROOT" --format=ustar -czf "$STAGE/$BUNDLE_ASSET_NAME" .
write_sha256sums "$STAGE/$BUNDLE_ASSET_NAME" "$STAGE/$CHECKSUMS_ASSET_NAME"
log "checksums written → $CHECKSUMS_ASSET_NAME"

[[ -f "$ROOT/scripts/curl_install.sh" ]] || die "缺少 scripts/curl_install.sh"
# Bake repo + tag into install.sh so shop machines can: curl …/install.sh | sudo -E bash
PUBLISH_REPO="${LUYUN_PUBLISH_REPO:-}"
if [[ -z "$PUBLISH_REPO" ]]; then
  PUBLISH_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
fi
if [[ -z "$PUBLISH_REPO" ]]; then
  PUBLISH_REPO="$(git -C "$ROOT" remote get-url origin 2>/dev/null | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
fi
[[ -n "$PUBLISH_REPO" ]] || die "无法解析 GitHub 仓库名（设 LUYUN_PUBLISH_REPO，或配置 gh / origin）"
# Use # delimiter so owner/name slashes need no escaping.
case "$PUBLISH_REPO$TAG" in
  *'#'*|*$'\n'*) die "REPO/TAG 含非法字符，无法写入 install.sh" ;;
esac
sed -e "s#^LUYUN_EMBEDDED_REPO=\"\"\$#LUYUN_EMBEDDED_REPO=\"${PUBLISH_REPO}\"#" \
    -e "s#^LUYUN_EMBEDDED_TAG=\"\"\$#LUYUN_EMBEDDED_TAG=\"${TAG}\"#" \
    "$ROOT/scripts/curl_install.sh" > "$STAGE/$INSTALL_ASSET_NAME"
grep -q "LUYUN_EMBEDDED_REPO=\"${PUBLISH_REPO}\"" "$STAGE/$INSTALL_ASSET_NAME" \
  || die "install.sh 未能写入内嵌 REPO"
grep -q "LUYUN_EMBEDDED_TAG=\"${TAG}\"" "$STAGE/$INSTALL_ASSET_NAME" \
  || die "install.sh 未能写入内嵌 TAG"
chmod 755 "$STAGE/$INSTALL_ASSET_NAME"
log "install.sh embedded repo=${PUBLISH_REPO} tag=${TAG}"

if git -C "$ROOT" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  TAG_SHA="$(git -C "$ROOT" rev-list -n 1 "$TAG")"
  [[ "$TAG_SHA" == "$HEAD_SHA" ]] || die "tag $TAG 已存在但不指向当前 HEAD ($HEAD_SHA)"
else
  log "create git tag $TAG at $HEAD_SHA"
  git -C "$ROOT" tag -a "$TAG" -m "Release $TAG" "$HEAD_SHA"
fi

# GitHub Release requires the tag's commit to exist on the remote. A local-only
# tag yields opaque API errors (422/500). Push the current branch tip, then the tag.
ensure_remote_commit_and_push_tag() {
  local commit="$1"
  if [[ "${LUYUN_PUBLISH_SKIP_PUSH:-}" == "1" ]]; then
    log "skip push (LUYUN_PUBLISH_SKIP_PUSH=1)"
    return 0
  fi
  log "ensure origin has commit $commit (push current branch if needed)"
  # Push HEAD so the release target commit is visible to GitHub.
  if ! git -C "$ROOT" push -u origin HEAD; then
    die "无法 push 当前分支到 origin；请先手动: git push -u origin HEAD"
  fi
  # Verify origin can see the commit (any remote-tracking branch).
  git -C "$ROOT" fetch origin --quiet || true
  if ! git -C "$ROOT" branch -r --contains "$commit" | grep -q .; then
    die "origin 仍看不到 $commit；请确认已 push 且远程为 origin"
  fi
  log "push tag $TAG to origin"
  git -C "$ROOT" push origin "refs/tags/$TAG" \
    || die "无法 push tag $TAG；若远端已有同名不同内容的 tag，请先处理冲突"
}

ensure_remote_commit_and_push_tag "$HEAD_SHA"

log "create GitHub Release $TAG (target $HEAD_SHA)"
gh release create "$TAG" \
  "$STAGE/$BUNDLE_ASSET_NAME" \
  "$STAGE/$CHECKSUMS_ASSET_NAME" \
  "$STAGE/$INSTALL_ASSET_NAME" \
  --target "$HEAD_SHA" \
  --title "$TAG" \
  --generate-notes

log "published Release $TAG with $BUNDLE_ASSET_NAME + $CHECKSUMS_ASSET_NAME + $INSTALL_ASSET_NAME"
