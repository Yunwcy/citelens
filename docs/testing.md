# 測試與可重現性

三層互補，缺一層就會漏掉一類問題。這個系統會出的錯多數不會當機 ——
表格挪一格、答案答錯題，全都「跑得動」，所以「跑得動」不是證據。

| 層 | 涵蓋 | 抓不到什麼 |
|---|---|---|
| `pytest` | 元件邏輯、解析正確性、不變量 | 反向代理、真實 HTTP、容器行為 |
| `check_docs.py` | 文件數字與產出一致 | 程式行為 |
| `e2e.py` | 全鏈路：上傳大小限制、SSE、重啟保存、健康檢查、外部服務中斷 | 視覺呈現 |

`pytest` 以 TestClient 在同一行程內執行，繞過反向代理；`scripts/e2e.py`
一律連 nginx，涵蓋上傳大小限制、健康檢查代理、SSE 緩衝等只存在於代理層的行為。

---

## 常用指令

```bash
cd backend && python -m pytest tests -q                      # 68 項測試
python scripts/check_docs.py                                 # 文件數字與產出一致
python scripts/e2e.py --offline --generality --full          # 端到端驗收（經 nginx）
```

從零建置驗證（相依是否都有宣告，而非本機剛好有）：

```bash
bash scripts/verify_clean_build.sh                           # 約 10–20 分鐘
```

`--no-cache` 會忽略所有既有層重建，是唯一能發現「相依沒宣告好、只是本機剛好有」的方式。

### 端到端驗收的選項

| 選項 | 額外涵蓋 |
|---|---|
| （無） | 服務、上傳、代表性查詢、引用、文件隔離 |
| `--offline` | 模型無法連線時索引仍須成功（短暫重啟後端） |
| `--generality` | 以其他三篇論文驗證與文件無關的規則 |
| `--full` | 重啟後資料保存、停用後端時健康檢查須失敗 |
| `--cold` | 先 down 再 up，量測冷啟動 |

---

## 作業限制由測試強制

三項限制有一個共同性質：**違反它們不會讓任何東西壞掉，只會讓系統看起來更好**。
把 PDF 直接交給模型會得到更完整的答案；改用外部向量化服務會省下 220MB 的映像檔；
把 `system_reserved` 調小會讓檢索額度變大。每一種違反都通過既有測試。

因此三項限制本身也寫成了會失敗的測試：

| 測試 | 保證 |
|---|---|
| `test_constraints.py::test_只有一個檔案能連到外部模型服務` | 解析、切塊、向量化皆不觸及外部服務；模型呼叫只存在於 `llm/client.py` |
| `test_constraints.py::test_沒有使用任何現成的文件解析型_API` | 禁用 `files.create`、`responses.create`、`input_file`、`image_url` 等入口 |
| `test_constraints.py::test_模型介面只收字串只回字串` | 模型介面無非文字入口 |
| `test_constraints.py::test_送進模型的訊息內容都是純文字` | 以 AST 確認 `messages` 的 `content` 不是多模態列表 |
| `test_constraints.py::test_向量化沒有外部後端可選` | 向量化後端清單中不存在外部服務選項 —— 預設值不是保證 |
| `test_budget.py::test_系統提示放得進保留額度` | `system_reserved` 反映提示詞的實際 token 數，預算算式中沒有估計值 |
| `test_budget.py::test_總量在最壞情況下也不超過作業上限` | 提示詞＋脈絡＋問題＋回答的最壞總量 ≤ 10,000 |

最後一項處理的是一個不明顯的缺口：問題長度上限設在**字元**，但脈絡上限以 token 計。
同樣 1,000 個字元的 token 數取決於用字 —— 實測英文約 223、常見中文約 1,779、
罕見漢字約 2,499、emoji 可達 3,000，皆遠超過 200 的保留額。
因此檢索額度改為依每次請求的實際問題長度計算（`Settings.budget_for`）。

寫這幾個測試時先讓它們失敗過一次：放一個會呼叫 `OpenAI().files.create()`
的檔案進 `app/`，確認測試確實變紅，再刪掉。一個永遠不會失敗的測試，
看起來跟一個有效的測試一模一樣。

---

## 重現量測結果

```bash
bash scripts/fetch_test_docs.sh                              # 取得測試文件
python scripts/eval.py --md docs/results/retrieval.md        # 檢索、表格、泛用性
python scripts/loadtest.py --md docs/results/load.md         # 併發
python scripts/bench_embedding.py onnx e5-small bge-zh zh    # 向量化模型選型
python scripts/report.py --container --md docs/results/runtime.md  # 執行指標
```

`docs/results/` 底下每一份報表都由上述指令產生，沒有一個數字是手打的。
README 引用的數字由 `scripts/check_docs.py` 逐項核對，CI 於每次推送執行 —— 對不上即失敗：

```bash
python scripts/check_docs.py        # 核對
python scripts/check_docs.py --fix  # 自動修正測試數量
```

指標檔會跨設定累積，`report.py` 會排除脈絡預算調整前產生的紀錄並標明筆數 ——
混在一起算，分母會變成兩個時期的混合值，而報表看起來完全正常。

---

## 產生監控資料

時間序列面板需要一段連續的流量才看得出趨勢：

```bash
python scripts/seed_metrics.py --minutes 7        # 分散送出查詢，輪流走各路由
python scripts/eval.py --publish                  # 發布檢索準確度與表格驗證結果
```

評估結果存於 `/data/eval.json`，指標紀錄存於 `/data/metrics.jsonl`；
兩者皆於啟動時還原，重啟不會讓儀表板變空。
