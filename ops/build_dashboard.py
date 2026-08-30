"""產生 Grafana 儀表板。

以程式生成而非手刻 JSON：九個面板的樣式與間距才會一致，
調整版面時也不必逐一修改。
"""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
GREEN, AMBER, CLAY, GRAY = "#1D7A5F", "#D9A441", "#C0847B", "#8A9A94"


def target(expr, legend, instant=False):
    return {"refId": "A", "expr": expr, "legendFormat": legend,
            "datasource": DS, "instant": instant, "range": not instant}


def panel(pid, title, targets, x, y, w, h, ptype="timeseries", desc="", **kw):
    p = {"id": pid, "title": title, "type": ptype, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [dict(t, refId=chr(65 + i)) for i, t in enumerate(targets)],
         "description": desc}
    p.update(kw)
    return p


def series(unit="short", **defaults):
    """時間序列的共用樣式：細線、淡填色、圖例置底。"""
    return {
        "defaults": {"unit": unit, "custom": {
            "lineWidth": 2, "fillOpacity": 8, "showPoints": "never",
            "gradientMode": "opacity", "axisBorderShow": False,
            "axisGridShow": True, "spanNulls": True}, **defaults},
        "overrides": [],
    }


LEGEND = {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []}
TOOLTIP = {"mode": "multi", "sort": "desc"}

panels = [
    # ── 第一列：品質指標（離線評估）─────────────────────────────
    panel(1, "檢索準確度",
          [target("citegrain_eval_top3_rate", "{{config}}", instant=True)],
          0, 0, 9, 7, "bargauge",
          desc="12 個測試問題中，目標章節進入前三名的比例。由 scripts/eval.py --publish 發布。",
          options={"displayMode": "basic", "orientation": "horizontal",
                   "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                   "showUnfilled": True, "minVizHeight": 26, "maxVizHeight": 40,
                   "namePlacement": "top", "valueMode": "color", "sizing": "manual"},
          fieldConfig={"defaults": {"unit": "percentunit", "min": 0, "max": 1, "decimals": 0,
                                    "color": {"mode": "thresholds"},
                                    "thresholds": {"mode": "absolute", "steps": [
                                        {"color": CLAY, "value": None},
                                        {"color": AMBER, "value": 0.5},
                                        {"color": GREEN, "value": 0.85}]}},
                       "overrides": []}),

    panel(2, "表格解析結果",
          [target("citegrain_eval_tables", "{{state}}", instant=True)],
          9, 0, 8, 7, "bargauge",
          desc="四篇論文合計。數值型表格全數通過驗證；未通過者退回整表原文模式。",
          options={"displayMode": "basic", "orientation": "horizontal",
                   "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                   "showUnfilled": True, "minVizHeight": 20, "namePlacement": "left",
                   "valueMode": "text", "sizing": "manual"},
          fieldConfig={"defaults": {"unit": "short", "min": 0, "max": 18, "decimals": 0,
                                    "color": {"mode": "fixed", "fixedColor": GREEN}},
                       "overrides": []}),

    panel(3, "有作答時的引用標註率",
          [target('sum(citegrain_answer_outcome_total{outcome="cited"}) / '
                  'sum(citegrain_answer_outcome_total{outcome=~"cited|uncited"})',
                  "引用率", instant=True)],
          17, 0, 7, 7, "stat",
          desc="分母排除「文件未涵蓋」的正確拒答 —— 那類回答沒有可標註的來源。",
          options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                   "colorMode": "value", "graphMode": "none", "textMode": "value",
                   "justifyMode": "center", "text": {"valueSize": 64}},
          fieldConfig={"defaults": {"unit": "percentunit", "min": 0, "max": 1, "decimals": 0,
                                    "color": {"mode": "thresholds"},
                                    "thresholds": {"mode": "absolute", "steps": [
                                        {"color": CLAY, "value": None},
                                        {"color": GREEN, "value": 0.9}]}},
                       "overrides": []}),

    # ── 第二列：延遲 ──────────────────────────────────────────
    panel(4, "查詢量（依路由）",
          [target("sum by (route) (increase(citegrain_query_total[5m]))", "{{route}}")],
          0, 7, 8, 7, desc="三條路由各自的查詢量。分流有效時三者都會有量。",
          options={"legend": LEGEND, "tooltip": TOOLTIP},
          fieldConfig=series("short", decimals=0)),

    panel(5, "端到端延遲",
          [target("histogram_quantile(0.5, sum(rate(citegrain_request_seconds_bucket[10m])) by (le))", "p50"),
           target("histogram_quantile(0.95, sum(rate(citegrain_request_seconds_bucket[10m])) by (le))", "p95")],
          8, 7, 8, 7, desc="使用者感受到的總時間。",
          options={"legend": LEGEND, "tooltip": TOOLTIP},
          fieldConfig=series("s", decimals=1)),

    panel(6, "延遲拆解：檢索 vs 生成",
          [target("histogram_quantile(0.95, sum(rate(citegrain_retrieval_seconds_bucket[10m])) by (le))", "檢索 p95"),
           target("histogram_quantile(0.95, sum(rate(citegrain_llm_seconds_bucket[10m])) by (le))", "生成 p95")],
          16, 7, 8, 7,
          desc="檢索貼著底部、生成在數秒之譜 —— 瓶頸在模型不在檢索。",
          options={"legend": LEGEND, "tooltip": TOOLTIP},
          fieldConfig=series("s", decimals=1)),

    # ── 第三列：預算與成本 ────────────────────────────────────
    panel(7, "脈絡用量（上限 6,000 tokens）",
          [target("histogram_quantile(0.5, sum(rate(citegrain_context_tokens_bucket[10m])) by (le))", "p50"),
           target("histogram_quantile(0.95, sum(rate(citegrain_context_tokens_bucket[10m])) by (le))", "p95")],
          0, 14, 9, 7,
          desc="檢索內容實際佔用的 token 數。不含系統提示與問題 —— "
               "那兩項另有保留額度，合計才是模型的 10,000 上限。",
          options={"legend": LEGEND, "tooltip": TOOLTIP},
          fieldConfig={"defaults": {"unit": "short", "decimals": 0, "max": 6000, "min": 0,
                                    "custom": {"lineWidth": 2, "fillOpacity": 8,
                                               "showPoints": "never", "gradientMode": "opacity",
                                               "axisBorderShow": False, "spanNulls": True,
                                               "thresholdsStyle": {"mode": "dashed"}},
                                    "thresholds": {"mode": "absolute", "steps": [
                                        {"color": "transparent", "value": None},
                                        {"color": AMBER, "value": 6000}]}},
                       "overrides": []}),

    panel(8, "累計成本與查詢次數",
          [target("citegrain_cost_usd_total", "累計成本（USD）", instant=True),
           target("sum(citegrain_query_total)", "查詢次數", instant=True)],
          9, 14, 8, 7, "stat",
          desc="索引階段不呼叫外部 API，成本僅來自查詢時的模型生成。",
          options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                   "colorMode": "none", "graphMode": "none", "textMode": "value_and_name",
                   "justifyMode": "center", "orientation": "horizontal",
                   "text": {"valueSize": 38, "titleSize": 14}},
          fieldConfig={"defaults": {"color": {"mode": "fixed", "fixedColor": GREEN}},
                       "overrides": [
                           {"matcher": {"id": "byName", "options": "累計成本（USD）"},
                            "properties": [{"id": "unit", "value": "currencyUSD"},
                                           {"id": "decimals", "value": 4}]},
                           {"matcher": {"id": "byName", "options": "查詢次數"},
                            "properties": [{"id": "unit", "value": "short"},
                                           {"id": "decimals", "value": 0},
                                           {"id": "color", "value": {"mode": "fixed",
                                                                     "fixedColor": GRAY}}]}]}),

    panel(9, "回答結果分布",
          [target("citegrain_answer_outcome_total", "{{outcome}}", instant=True)],
          17, 14, 7, 7, "bargauge",
          desc="cited 有標註引用 · uncited 有作答但未標註 · declined 文件未涵蓋而如實拒答。",
          options={"displayMode": "basic", "orientation": "horizontal",
                   "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                   "showUnfilled": True, "minVizHeight": 22, "maxVizHeight": 40,
                   "namePlacement": "top", "valueMode": "text", "sizing": "manual"},
          fieldConfig={"defaults": {"unit": "short", "decimals": 0, "min": 0,
                                    "color": {"mode": "fixed", "fixedColor": GRAY}},
                       "overrides": [
                           {"matcher": {"id": "byName", "options": "cited"},
                            "properties": [{"id": "color", "value": {"mode": "fixed",
                                                                     "fixedColor": GREEN}}]},
                           {"matcher": {"id": "byName", "options": "uncited"},
                            "properties": [{"id": "color", "value": {"mode": "fixed",
                                                                     "fixedColor": CLAY}}]}]}),
]

dashboard = {
    "uid": "citegrain", "title": "CiteGrain", "tags": ["citegrain"],
    "timezone": "browser", "schemaVersion": 39, "version": 2,
    "refresh": "10s", "editable": True,
    "time": {"from": "now-30m", "to": "now"},
    "panels": panels,
}

out = Path(__file__).parent / "grafana/provisioning/dashboards/citegrain.json"
out.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已產生 {out.name}：{len(panels)} 個面板，三列各 7 單位高")
