# CiteLens

**受限脈絡下的長文件溯源問答系統**

上傳 PDF 或貼上 arXiv 連結，針對文件內容提問，每個回答都附上頁碼與章節出處。

在假設語言模型脈絡上限僅有 10K tokens 的前提下，處理數萬 token 的長文件，
並確保表格數值不因解析而失真。

---

## 設計限制

本專案在三項限制下實作，這些限制決定了絕大多數的架構選擇：

| 限制 | 對應設計 |
|---|---|
| 不得使用商用 API 解析文件 | 版面分析、章節偵測、表格抽取、向量化全部在本地執行 |
| 語言模型只做 text-to-text | 全專案僅有一處呼叫模型，且介面只接受字串 |
| 假設脈絡上限 10K tokens | 檢索內容硬性限制在 7,000 tokens，並實作 token 感知的內容組裝 |

---

## 核心做法

- **結構感知切塊** — 以文件自身的章節階層切分，而非固定長度；表格為不可分割單位
- **表格幾何抽取** — 以版面線條為錨點還原儲存格，並以數值多重集守恆驗證抽取正確性
- **混合檢索** — 向量與 BM25 各自檢索後以 RRF 融合
- **查詢路由** — 摘要、比較、表格查詢、一般問答各走不同策略
- **階層式摘要** — 以章節為單位 map-reduce，結果快取
- **脈絡預算管理** — 依分數組裝檢索內容，超出預算時裁切並記錄

---

## 執行方式

```bash
cp .env.example .env        # 填入 OPENAI_API_KEY
docker compose up --build
```

| 服務 | 位置 |
|---|---|
| 介面 | http://localhost:3000 |
| API | http://localhost:8000 |
| 指標 | http://localhost:8000/api/metrics |
| Grafana | http://localhost:3001 |

不使用容器時：

```bash
python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/uvicorn app.main:app --app-dir backend --port 8000
cd frontend && npm install && npm run dev
```

## 成果數據

四篇不同排版的論文實測，全部可由指令重現，報表位於 `docs/results/`。

### 檢索準確度（對系統自身做消融）

| 查詢 | baseline | ＋結構感知 | 完整系統 |
|---|---|---|---|
| summary this document | 未進前 10 | 2 | 2 |
| compare lightRAG with GraphRAG | 未進前 10 | 1（涵蓋 2/2） | 1（涵蓋 2/2） |
| Performance of ablated versions | 未進前 10 | 未進前 10 | **1** |
| 消融實驗的結果如何？ | 未進前 10 | 1 | 1 |
| LightRAG 和 GraphRAG 有什麼差別 | 未進前 10 | 1（涵蓋 1/2） | **1（涵蓋 2/2）** |

baseline 為固定長度切塊、純向量檢索、不做表格處理。
第三題的答案本身位於表格內，唯有表格抽取能命中。

### 表格保真度

- 偵測到 17 張表：數值型 13 張、敘述型 2 張、驗證失敗 2 張
- **數值型表格驗證通過率 13/13**
- **錯誤的儲存格進入索引的比率為 0** —— 未通過驗證者一律清空儲存格、
  僅保留整表原文。這是結構保證，不是統計結果

### 泛用性

| 文件 | 頁 | 欄 | 章節來源 | 章節數 |
|---|---:|---:|---|---:|
| 1706.03762 | 15 | 1 | PDF 大綱 | 22 |
| 1810.04805 | 16 | **2** | **編號規則** | 23 |
| 2005.11401 | 19 | 1 | PDF 大綱 | 29 |
| 2410.05779 | 16 | 1 | PDF 大綱 | 26 |

其中一篇無 PDF 內建大綱，由級聯的第二級接手。

### 併發

| 情境 | 檢索 p50 | 檢索 p95 | 端到端 p50 |
|---|---:|---:|---:|
| 1 併發 | 25 ms | 25 ms | 0.8 s |
| 20 併發 | 98 ms | 157 ms | 3.2 s |
| 20 併發 ＋ 同時上傳 | 92 ms | 142 ms | 2.6 s |

### 成本

單次查詢平均 US$0.00075；索引階段外部 API 呼叫 0 次（向量化全程本地）。

## 重現這些數字

```bash
python scripts/eval.py --md docs/results/retrieval.md        # 檢索、表格、泛用性
python scripts/loadtest.py --md docs/results/load.md         # 併發
python scripts/bench_embedding.py onnx e5-small bge-zh zh    # 向量化模型選型
python scripts/report.py --md docs/results/runtime.md        # 執行指標
cd backend && python -m pytest tests -q                      # 36 項測試
```

## 對外介面

- REST 與 SSE：`/api/documents`、`/api/query`、`/api/jobs/{id}/events`
- Prometheus：`/api/metrics`
- MCP server：`python -m app.mcp_server`，提供 `search_document`、
  `get_table_cell`、`summarize_document`、`list_documents` 四個工具。
  `get_table_cell` 為確定性查表，值取自結構化索引而非模型生成

---

## 專案結構

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
```

---

## 未實作項目

雲端部署、知識圖譜檢索、模型重排序、多文件同時檢索、
以視覺語言模型進行版面分析（目標文件為 digital-born PDF，文字層完整）。

有兩張表格（不在指定文件內）未通過解析驗證，已退回整表原文模式；
其失敗模式分別為合併儲存格與多層欄位，修正需改動核心對齊邏輯。
