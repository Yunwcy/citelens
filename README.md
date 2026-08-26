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
python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python scripts/smoke_llm.py
```

容器化與前端啟動方式將於完成後補上。

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

## 開發狀態

實作中。已完成：專案骨架、參數集中管理、模型呼叫通道。
