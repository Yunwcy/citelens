#!/usr/bin/env bash
# 下載測試用論文。這些檔案不進版控（授權不明），由 CI 與本機自行取得。
set -euo pipefail
DIR="$(dirname "$0")/../_working/testdocs"
mkdir -p "$DIR"
for id in 2410.05779 1810.04805 1706.03762 2005.11401; do
  [ -f "$DIR/$id.pdf" ] && continue
  echo "下載 $id"
  curl -sL -o "$DIR/$id.pdf" "https://arxiv.org/pdf/$id"
done
ls -la "$DIR"
