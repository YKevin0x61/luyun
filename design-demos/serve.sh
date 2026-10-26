#!/usr/bin/env bash
# 本地预览：三个方向的原型 + 缩略图总览
# 用法：bash serve.sh   → 打开 http://127.0.0.1:8899/
set -e
cd "$(dirname "$0")"
PORT="${1:-8899}"
echo "预览地址：http://127.0.0.1:${PORT}/"
echo "  缩略图：  http://127.0.0.1:${PORT}/thumbs.html"
echo "  方向一：  http://127.0.0.1:${PORT}/dir-1/index.html"
echo "  方向二：  http://127.0.0.1:${PORT}/dir-2/index.html"
echo "  方向三：  http://127.0.0.1:${PORT}/dir-3/index.html"
echo "（Ctrl+C 结束）"
python3 -m http.server "${PORT}" --bind 127.0.0.1
