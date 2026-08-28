# CiteLens

**受限脈絡下的長文件溯源問答系統**

上傳 PDF 或貼上 arXiv 連結，以自然語言提問，每個回答都附上頁碼與章節出處。
文件解析、章節切分、表格還原與向量化全部在本地完成；語言模型只負責把
整理好的文字寫成答案。

在假設模型脈絡上限僅 10K tokens 的前提下處理數萬 token 的長文件，
並確保表格數值不因解析而失真。

---

## 功能

- **溯源回答** — 每個事實標註來源編號，側欄可展開對應的文件原文
- **表格問答** — 以版面幾何還原儲存格，數值可經確定性查表核對
- **全文摘要** — 以章節為單位 map-reduce，不受單次脈絡上限限制
- **中英雙語** — 介面與作答語言獨立；以何種語言提問即以該語言回答
- **由網址匯入** — arXiv 連結自動正規化並取回標題，含 SSRF 防護
- **可觀測** — Prometheus 指標與 Grafana 儀表板，涵蓋延遲、成本與檢索準確度
- **MCP server** — 以四個工具對外提供檢索與確定性查表

---

## 快速開始

需求：Docker 與 Docker Compose。

```bash
cp .env.example .env          # 填入 OPENAI_API_KEY
docker compose up -d --build
```

| 服務 | 位置 | 用途 |
|---|---|---|
| 介面 | http://localhost:3000 | 上傳文件、提問、檢視引用來源 |
| API 文件 | http://localhost:8000/docs | 端點的參數與回應格式，可直接於頁面試打 |
| 指標 | http://localhost:8000/api/metrics | Prometheus 格式的查詢次數、延遲、成本、準確度 |
| 儀表板 | http://localhost:3001 | 上述指標的視覺化 |

不使用容器時：

```bash
python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/uvicorn app.main:app --app-dir backend --port 8000
cd frontend && npm install && npm run dev
```

### 設定

所有可調參數集中於 `backend/app/config.py`，可由環境變數覆寫。

| 參數 | 預設 | 說明 |
|---|---|---|
| `OPENAI_API_KEY` | — | 必填 |
| `LLM_MODEL` | `gpt-4o-mini` | 僅用於 text-to-text |
| `MAX_CONTEXT` | `10000` | 假設的模型脈絡上限 |
| `CHUNK_TARGET_TOKENS` | `450` | 片段目標長度 |
| `TOP_K` | `8` | 融合後取用的片段數 |
| `EMBEDDING_BACKEND` | `onnx` | 本地向量化，不經外部服務 |

檢索可用的脈絡預算由上述參數推導而得，不另行設定：

```python
retrieval_budget = max_context − system_reserved − question_reserved
                   − answer_reserved − safety_margin      # = 7,000
```

---

## 設計約束

三項約束決定了絕大多數的架構選擇：

| 約束 | 理由 | 對應設計 |
|---|---|---|
| 文件解析不依賴外部服務 | 文件內容不外流，且結果可重現、不隨供應商版本變動 | 版面分析、章節偵測、表格抽取、向量化全部本地執行 |
| 語言模型只做 text-to-text | 不綁定特定供應商的多模態能力，換模型不必改動管線 | 全專案僅一處呼叫模型，介面只接受與回傳字串 |
| 脈絡上限視為 10K tokens | 小脈絡模型與長文件的組合是常態，不應假設能整份塞入 | 檢索內容硬性限制於 7,000 tokens，並實作 token 感知的組裝 |

---

## 運作方式

```
上傳 ─▶ 版面分析 ─▶ 章節偵測 ─▶ 表格抽取＋驗證 ─▶ 切塊 ─▶ 向量與 BM25 索引
                                                                  │
提問 ─▶ 查詢路由 ─▶ 混合檢索 ─▶ RRF 融合 ─▶ 脈絡預算組裝 ─▶ 模型 ─▶ 引用擷取
```

- **結構感知切塊** — 依文件自身的章節階層切分而非固定長度；章節偵測為四級級聯
  （PDF 大綱 → 編號規則 → 字級分群 → 遞迴切分），表格為不可分割單位
- **表格幾何抽取** — 以版面線條為錨點還原儲存格；數值多重集守恆與鍵唯一性
  兩項驗證皆通過才進入索引，否則整表退回原文模式
- **混合檢索** — 向量與 BM25 各自檢索後以 RRF（k=60）融合
- **查詢路由** — 摘要、比較、表格查詢、一般問答各走不同策略
- **階層式摘要** — 以章節為單位 map-reduce，章節摘要與語言無關且僅計算一次
- **脈絡預算管理** — 依分數逐段組裝，超出預算時於句界裁切並記錄；表格永不裁切

詳細設計見 [`docs/architecture.md`](docs/architecture.md)。

---

## 量測結果

四篇不同排版的論文實測，全部可由指令重現，報表位於 [`docs/results/`](docs/results/)。

### 檢索準確度

12 個測試問題（部分含多個期望章節，合計 22 項判定），涵蓋 7 個章節、中英文各半。
判準為目標章節是否進入檢索結果前三名。以系統自身做消融：

| 設定 | 命中前三名 |
|---|---|
| 基準：固定視窗切塊 · 純向量檢索 · 不處理表格 | **0%** |
| ＋結構感知切塊 | **75%** |
| 完整系統：＋混合檢索 ＋表格處理 | **95%**（21/22） |

其中「消融版本的效能」一題唯有表格處理能命中 —— 該題答案位於表格內。

跨語言查詢（以中文提問英文文件）初期明顯較差：關鍵字檢索在跨語言時
無法貢獻，等同僅剩向量檢索。將章節標題併入向量化內容後，
該類查詢的排名由第 6、第 9 名提升至第 1 名。

### 表格保真度

- 偵測到 17 張表：數值型 13 張、敘述型 2 張、驗證未通過 2 張
- **數值型表格驗證通過率 13/13**
- **錯誤儲存格進入索引的比率為 0** —— 未通過驗證者一律清空儲存格、
  僅保留整表原文。這是結構保證，不是統計結果

### 泛用性

| 文件 | 頁 | 欄 | 章節來源 | 章節數 |
|---|---:|---:|---|---:|
| 1706.03762 | 15 | 1 | PDF 大綱 | 23 |
| 1810.04805 | 16 | **2** | **編號規則** | 24 |
| 2005.11401 | 19 | 1 | PDF 大綱 | 30 |
| 2410.05779 | 16 | 1 | PDF 大綱 | 27 |

其中一篇無 PDF 內建大綱，由級聯的第二級接手。

### 併發

| 情境 | 檢索 p50 | 檢索 p95 | 端到端 p50 |
|---|---:|---:|---:|
| 1 併發 | 28 ms | 28 ms | 3.5 s |
| 5 併發 | 30 ms | 35 ms | 1.7 s |
| 20 併發 | 68 ms | 92 ms | 5.0 s |
| 20 併發 ＋ 同時上傳 | 84 ms | 108 ms | 6.1 s |

端到端時間由模型生成主導，屬於外部延遲；`retrieval_ms` 才是本服務自身控制的部分。

索引與查詢使用各自獨立的推論工作階段。僅做排程隔離時，查詢的單句向量化
仍會排在整批片段之後，實測檢索中位數會由 44 ms 升至 733 ms。

### 回答品質

| 結果 | 次數 |
|---|---:|
| 有標註引用 | 100 |
| 有作答但未標註引用 | **0** |
| 文件未涵蓋而如實拒答 | 24 |

引用率的分母排除拒答 —— 文件未涵蓋該問題時模型應如實說明，
這類回答本來就沒有可標註的來源，計入會把正確行為算成失敗。

### 成本

單次查詢平均 US$0.00093，脈絡用量中位數 2,243 / 7,000 tokens；
索引階段外部 API 呼叫 0 次（向量化全程本地）。

---

## 對外介面

REST 與 SSE：

| 端點 | 說明 |
|---|---|
| `POST /api/documents` | 上傳 PDF，回傳 job 與 doc id |
| `POST /api/documents/from-url` | 由 arXiv 或 PDF 連結匯入 |
| `GET /api/documents` | 文件清單 |
| `GET /api/documents/{id}` | 文件資訊、表格清單、建議問題 |
| `DELETE /api/documents/{id}` | 刪除文件、索引與摘要快取 |
| `GET /api/documents/{id}/summary` | 全文摘要 |
| `POST /api/query` | 問答（SSE 串流） |
| `GET /api/jobs/{id}/events` | 索引進度（SSE 串流） |
| `GET /api/metrics` | Prometheus 指標 |

MCP server：

```bash
python -m app.mcp_server
```

提供 `search_document`、`get_table_cell`、`summarize_document`、`list_documents`
四個工具。`get_table_cell` 為確定性查表，值取自結構化索引而非模型生成。

---

## 開發

```bash
cd backend && python -m pytest tests -q                      # 61 項測試
python scripts/check_docs.py                                 # 文件數字與產出一致
python scripts/e2e.py --offline --generality --full          # 端到端驗收（經 nginx）
```

從零建置驗證（相依是否都有宣告，而非本機剛好有）：

```bash
bash scripts/verify_clean_build.sh                           # 約 10–20 分鐘
```

三層互補，缺一層就會漏掉一類問題：

| 層 | 涵蓋 | 抓不到什麼 |
|---|---|---|
| `pytest` | 元件邏輯、解析正確性、不變量 | 反向代理、真實 HTTP、容器行為 |
| `check_docs.py` | 文件數字與產出一致 | 程式行為 |
| `e2e.py` | 全鏈路：上傳大小限制、SSE、重啟保存、健康檢查、外部服務中斷 | 視覺呈現 |

`pytest` 以 TestClient 在同一行程內執行，繞過反向代理；`scripts/e2e.py`
一律連 nginx，涵蓋上傳大小限制、健康檢查代理、SSE 緩衝等只存在於代理層的行為。

| 選項 | 額外涵蓋 |
|---|---|
| （無） | 服務、上傳、代表性查詢、引用、文件隔離 |
| `--offline` | 模型無法連線時索引仍須成功（短暫重啟後端） |
| `--generality` | 以其他三篇論文驗證與文件無關的規則 |
| `--full` | 重啟後資料保存、停用後端時健康檢查須失敗 |
| `--cold` | 先 down 再 up，量測冷啟動 |

產生監控資料（時間序列面板需要一段連續的流量才看得出趨勢）：

```bash
python scripts/seed_metrics.py --minutes 7        # 分散送出查詢，輪流走各路由
python scripts/eval.py --publish                  # 發布檢索準確度與表格驗證結果
```

評估結果會存於 `/data/eval.json`，指標紀錄存於 `/data/metrics.jsonl`；
兩者皆於啟動時還原，重啟不會讓儀表板變空。

重現量測結果：

```bash
bash scripts/fetch_test_docs.sh                              # 取得測試文件
python scripts/eval.py --md docs/results/retrieval.md        # 檢索、表格、泛用性
python scripts/loadtest.py --md docs/results/load.md         # 併發
python scripts/bench_embedding.py onnx e5-small bge-zh zh    # 向量化模型選型
python scripts/report.py --container --md docs/results/runtime.md  # 執行指標
```

`docs/results/` 底下每一份報表都由上述指令產生。README 引用的數字
由 `scripts/check_docs.py` 逐項核對，CI 於每次推送執行 —— 對不上即失敗：

```bash
python scripts/check_docs.py        # 核對
python scripts/check_docs.py --fix  # 自動修正測試數量
```

### 專案結構

```
backend/app/
├─ config.py          參數集中管理，評估對照組由此切換
├─ llm/client.py      全專案唯一的模型呼叫出口
├─ util/tokens.py     統一以 cl100k_base 計數
├─ parser/            PDF 解析、版面分析、表格抽取
├─ chunking/          章節偵測與切塊
├─ retrieval/         向量、BM25、融合、脈絡預算
├─ router/            查詢路由
├─ summarization/     階層式摘要
└─ api/               HTTP 介面
frontend/src/         React 介面
ops/                  Prometheus 與 Grafana 設定
scripts/              評估、壓測、端到端驗收
```
