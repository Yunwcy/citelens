# 向量化模型選型實測

文件：arXiv 2410.05779（LightRAG），90 個片段。
指標：目標章節在檢索結果中的排名（越小越好，✓ 表示進入前三）。
重現：`python scripts/bench_embedding.py onnx e5-small bge-zh zh`

| 後端 | 模型 | 磁碟 | 建索引 | 查詢中位數 | EN 比較 | EN 消融 | ZH 消融 | ZH 比較 |
|---|---|---:|---:|---:|:--:|:--:|:--:|:--:|
| **onnx（採用）** | paraphrase-multilingual-MiniLM-L12-v2 | 220MB | **11.7s** | 22ms | **1** | **1** | **1** | **1** |
| e5-small | intfloat/multilingual-e5-small | 470MB | 60.2s | 24ms | 2 | **1** | 4 | 3 |
| bge-zh | BAAI/bge-small-zh-v1.5 | 90MB | 26.4s | 16ms | **1** | **1** | 8 | 4 |
| zh | jinaai/jina-embeddings-v2-base-zh | 640MB | 297.7s | 51ms | **1** | **1** | 4 | **1** |

查詢：
1. `compare lightRAG with GraphRAG` → 期望命中 Comparison 章節
2. `Performance of ablated versions of LightRAG` → 期望命中 Ablation 章節
3. `消融實驗的結果如何？` → 同上，測跨語言檢索
4. `LightRAG 和 GraphRAG 有什麼差別` → 同上

## 結論

採用 **paraphrase-multilingual-MiniLM-L12-v2**：四個查詢全數排名第一，
建索引速度為次佳者的兩倍以上，體積 220MB。

## 這個結果與預期相反

原本的判斷是「e5 系列是專為非對稱檢索訓練的較新模型，應優於 2019 年的相似度模型」，
初期實測也支持此判斷 —— 當時 MiniLM 在中文查詢上完全找不到正確章節。

實際原因是套件層的問題：fastembed 0.5.1 對此模型使用 CLS 池化，
而該模型應使用 mean 池化。0.8.0 修正後，同一個模型的表現完全改觀。

**若未升級套件並重測，會得到「這個模型不適合跨語言檢索」的錯誤結論，
並據此付出五倍的建索引時間與兩倍的磁碟空間去換取更差的準確度。**

## 保留的切換路徑

四個後端皆保留於 `EMBEDDING_BACKEND`，可隨時重測。索引會記錄自身使用的後端，
查詢時沿用同一個 —— 混用不同後端會落在不同的向量空間，維度相同時甚至不會報錯。
