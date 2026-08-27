#!/usr/bin/env bash
# 從零建置並驗證。
#
# --no-cache 會忽略所有既有層重建：重新安裝相依套件、重新下載向量化模型。
# 這是唯一能發現「相依沒宣告好、只是本機剛好有」的方式 ——
# 平常的 build 會沿用快取層，把這類問題藏起來。
#
# 具名卷不受影響，因此文件、指標與評估結果會保留，
# 儀表板不需要重新產生資料。
#
# 需要網路（下載相依與模型），約 10–20 分鐘。
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

step "1/5　從零建置（無快取）"
time docker compose build --no-cache

step "2/5　啟動"
docker compose up -d
printf '   等待服務就緒'
for _ in $(seq 1 120); do
  if curl -sf localhost:3000/health >/dev/null 2>&1; then printf ' 就緒\n'; break; fi
  printf '.'; sleep 3
done
curl -sf localhost:3000/health >/dev/null || { echo '   服務未就緒'; exit 1; }

step "3/5　既有資料是否保留"
docker compose logs backend 2>&1 | grep -E "重放|評估結果" | tail -2 || echo '   （無既有紀錄，屬正常）'
curl -s localhost:3000/api/documents | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(f'   文件 {len(d)} 份')"

step "4/5　端到端驗收"
python scripts/e2e.py --offline --generality

step "5/5　文件數字一致"
python scripts/check_docs.py

printf '\n\033[1m從零建置驗證完成。\033[0m\n'
printf '接著開 http://localhost:3000 與 http://localhost:3001 目視確認。\n'
