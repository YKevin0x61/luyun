#!/usr/bin/env bash
# 构建 KDS uni-app H5 并部署到 public/kds/（FastAPI 挂载 /kds/）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/kds"
OUT="$ROOT/public/kds"

usage() {
  cat <<'EOF'
用法: scripts/build_kds.sh [选项]

构建 kds/ 的 H5 产物并复制到 public/kds/。

选项:
  -h, --help     显示此帮助
  --copy-only    跳过编译，仅从已有 dist 复制到 public/kds/
  --no-link      不自动 symlink HBuilderX node_modules

环境变量:
  UNI_BIN        指定 uni CLI 可执行文件路径
  HBX_ROOT       指定 HBuilderX 安装根目录（默认自动检测 macOS 路径）

依赖（任选其一）:
  1. HBuilderX（推荐）— macOS 默认 /Applications/HBuilderX.app
  2. kds/node_modules/.bin/uni — 本地 npm install 后可用

构建完成后访问: http://localhost:8000/kds/
EOF
}

COPY_ONLY=0
NO_LINK=0
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --copy-only) COPY_ONLY=1 ;;
    --no-link) NO_LINK=1 ;;
    *) echo "未知选项: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

log() { echo "==> $*"; }
die() { echo "错误: $*" >&2; exit 1; }

# ── 检测 HBuilderX 根目录 ──
detect_hbx_root() {
  if [[ -n "${HBX_ROOT:-}" && -d "$HBX_ROOT/plugins/uniapp-cli-vite" ]]; then
    echo "$HBX_ROOT"
    return 0
  fi
  local candidates=(
    "/Applications/HBuilderX.app/Contents/HBuilderX"
    "$HOME/Applications/HBuilderX.app/Contents/HBuilderX"
  )
  for dir in "${candidates[@]}"; do
    if [[ -d "$dir/plugins/uniapp-cli-vite" ]]; then
      echo "$dir"
      return 0
    fi
  done
  return 1
}

# ── 解析 uni CLI ──
resolve_uni_bin() {
  if [[ -n "${UNI_BIN:-}" ]]; then
    [[ -x "$UNI_BIN" ]] || die "UNI_BIN 不可执行: $UNI_BIN"
    echo "$UNI_BIN"
    return 0
  fi

  local local_bin="$SRC/node_modules/.bin/uni"
  if [[ -x "$local_bin" && ! -L "$SRC/node_modules" ]]; then
    echo "$local_bin"
    return 0
  fi

  local hbx_root=""
  hbx_root="$(detect_hbx_root)" || die "$(cat <<MSG
未找到 uni CLI。请任选其一:
  • 安装 HBuilderX（macOS）并在 PATH 可用
  • 设置 HBX_ROOT 指向 HBuilderX 目录
  • 设置 UNI_BIN 指向 uni 可执行文件
  • 在 kds/ 执行 npm install 安装 @dcloudio 依赖
MSG
)"

  local hbx_uni="$hbx_root/plugins/uniapp-cli-vite/node_modules/.bin/uni"
  [[ -x "$hbx_uni" ]] || die "HBuilderX 中未找到 uni CLI: $hbx_uni"
  echo "$hbx_uni"
}

# ── 确保 kds/node_modules（HBuilderX symlink）──
ensure_node_modules() {
  [[ "$NO_LINK" -eq 1 ]] && return 0
  [[ -e "$SRC/node_modules" ]] && return 0

  local hbx_root=""
  hbx_root="$(detect_hbx_root)" || return 0

  local hbx_node="$hbx_root/plugins/uniapp-cli-vite/node_modules"
  [[ -d "$hbx_node" ]] || return 0

  log "链接 HBuilderX node_modules → kds/node_modules"
  ln -sf "$hbx_node" "$SRC/node_modules"
}

# ── 查找构建产物目录 ──
find_build_dir() {
  local candidates=(
    "$SRC/dist/build/h5"
    "$SRC/unpackage/dist/build/h5"
  )
  for dir in "${candidates[@]}"; do
    if [[ -f "$dir/index.html" ]]; then
      echo "$dir"
      return 0
    fi
  done
  return 1
}

# ── 验证产物 ──
verify_build() {
  local dir="$1"
  [[ -f "$dir/index.html" ]] || die "缺少 index.html: $dir"
  [[ -d "$dir/assets" ]] || die "缺少 assets 目录: $dir"

  if ! grep -q '/kds/' "$dir/index.html"; then
    die "index.html 未包含 /kds/ 路径，请检查 kds/manifest.json h5.publicPath"
  fi
}

# ── 部署到 public/kds ──
deploy() {
  local build_dir="$1"
  log "复制 $build_dir → $OUT"
  rm -rf "$OUT"
  mkdir -p "$OUT"
  cp -R "$build_dir/"* "$OUT/"
  verify_build "$OUT"
}

# ── 主流程 ──
main() {
  [[ -d "$SRC" ]] || die "KDS 源码目录不存在: $SRC"
  [[ -f "$SRC/manifest.json" ]] || die "缺少 kds/manifest.json"

  if [[ "$COPY_ONLY" -eq 1 ]]; then
    local build_dir
    build_dir="$(find_build_dir)" || die "未找到已有构建产物，请先完整构建"
    deploy "$build_dir"
    log "完成（仅复制）。访问: http://localhost:8000/kds/"
    exit 0
  fi

  ensure_node_modules

  # HX_APP_ROOT 必须在主 shell export，否则 H5 页面不会进入 bundle（白屏）
  local hbx_root=""
  if hbx_root="$(detect_hbx_root)"; then
    export HX_APP_ROOT="$hbx_root"
  fi

  local uni_bin
  uni_bin="$(resolve_uni_bin)"

  log "编译 KDS H5"
  log "  源码: $SRC"
  log "  CLI:  $uni_bin"
  [[ -n "${HX_APP_ROOT:-}" ]] && log "  HBX:  $HX_APP_ROOT"

  cd "$SRC"
  UNI_INPUT_DIR="$SRC" UNI_PLATFORM=h5 "$uni_bin" build -p h5

  local build_dir
  build_dir="$(find_build_dir)" || die "构建完成但未找到产物（dist/build/h5 或 unpackage/dist/build/h5）"

  deploy "$build_dir"
  log "完成。访问: http://localhost:8000/kds/"
}

main "$@"
