"""表格抽取：以版面線條為幾何錨點還原儲存格。

為什麼不用 PyMuPDF 內建的 find_tables()：對目標論文實測後發現
- strategy="lines_strict"：幾乎抓不到，論文使用 booktabs，沒有垂直框線
- strategy="text"：每一頁的正文段落都被誤判成一張大表格

改採的做法是先用 get_drawings() 取得表格的精確邊界（LaTeX 的 \\toprule
與 \\bottomrule 是真實繪製的線條），再以字元座標還原列與欄。

抽取結果一律通過「數值多重集守恆」檢查：表格區域內出現的所有數值，
與還原後儲存格中的所有數值必須完全相同，否則標記為低信心並退回整表模式。
"""
from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter

import pymupdf

from app.models import DocumentProfile, Rule, Table
from app.parser.pdf_parser import ParsedPdf

_ROW_TOL = 3.5            # 同一列的 y 容差
_COL_GAP = 6.0            # 大於此間距視為換欄
_CLUSTER_GAP = 150.0      # 線條間距超過此值視為不同表格
_MIN_RULES = 3            # booktabs 至少有 top / mid / bottom
_CAPTION_WINDOW = 90.0    # 在表格上方多少距離內尋找標號
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_CAPTION = re.compile(r"(?:Table|TABLE|表)\s*(\d+)\s*[:.、]\s*(.*)", re.S)


def extract(pdf: ParsedPdf, prof: DocumentProfile) -> list[Table]:
    if not prof.has_text_layer:
        return []

    rules = pdf.rules()
    noise_ys = _running_rule_ys(rules, pdf.n_pages)
    tables: list[Table] = []

    for pno in range(1, pdf.n_pages + 1):
        h = sorted(
            (r for r in rules if r.page == pno and r.horizontal and round(r.y0) not in noise_ys),
            key=lambda r: r.y0,
        )
        v = [r for r in rules if r.page == pno and r.vertical]
        for cluster in _cluster(h):
            cluster = _consistent(cluster)
            if len(cluster) < _MIN_RULES:
                continue
            t = _extract_one(pdf, pno, cluster, v, f"T{len(tables) + 1}")
            if t is not None:
                tables.append(t)
    return tables


# --- 區域偵測 ---------------------------------------------------------------

def _running_rule_ys(rules: list[Rule], n_pages: int, ratio: float = 0.3) -> set[int]:
    """在多數頁面同一高度重複出現的橫線是頁首頁尾裝飾，不是表格線。"""
    if n_pages < 4:
        return set()
    counts = Counter(round(r.y0) for r in rules if r.horizontal)
    threshold = max(2, int(n_pages * ratio))
    return {y for y, c in counts.items() if c >= threshold}


def _consistent(cluster: list[Rule]) -> list[Rule]:
    """只保留左右端點一致的線條。

    真正的表格，其 toprule / midrule / bottomrule 幾乎等寬；混在同一段
    垂直距離內的圖表框線或其他裝飾線寬度不同。不做這道過濾時，區域的
    x 範圍會被撐大到整頁，把相鄰欄位的正文一起掃進來 —— 實測在論文
    第 9 頁就會產生一張由正文與圖說拼成的假表格。
    """
    if not cluster:
        return []
    key = Counter((round(r.x0 / 10), round(r.x1 / 10)) for r in cluster).most_common(1)[0][0]
    return [r for r in cluster if (round(r.x0 / 10), round(r.x1 / 10)) == key]


def _cluster(rules: list[Rule]) -> list[list[Rule]]:
    groups: list[list[Rule]] = []
    cur: list[Rule] = []
    for r in rules:
        if cur and (r.y0 - cur[-1].y0) > _CLUSTER_GAP:
            groups.append(cur)
            cur = []
        cur.append(r)
    if cur:
        groups.append(cur)
    return groups


# --- 單一表格 ---------------------------------------------------------------

def _extract_one(
    pdf: ParsedPdf, pno: int, cluster: list[Rule], v_rules: list[Rule], tid: str
) -> Table | None:
    page = pdf.doc[pno - 1]
    top, bottom = cluster[0].y0, cluster[-1].y0
    x0 = min(r.x0 for r in cluster)
    x1 = max(r.x1 for r in cluster)

    inner_v = [r for r in v_rules if top <= r.y0 <= bottom and x0 - 5 <= r.x0 <= x1 + 5]
    strategy = "lattice" if len(inner_v) >= 2 else "booktabs"

    grid = _grid(page, x0, top, x1, bottom)
    if len(grid) < 2:
        return None

    # booktabs 的第二條線是表頭與內文的分界
    mid = cluster[1].y0 if len(cluster) > 2 else top
    header_rows = [r for r in grid if r["y"] < mid]
    body_rows = [r for r in grid if r["y"] >= mid]
    if not body_rows:
        header_rows, body_rows = grid[:1], grid[1:]
    if not body_rows:
        return None

    template = _template(body_rows, inner_v, x0, x1)
    n_cols = len(template)
    if n_cols < 2:
        return None

    header_levels = [_assign_span(r["cells"], template, span=True) for r in header_rows]
    # 原始表頭要另外留一份：header_levels 會隨群組小標被替換，
    # 整表渲染若用替換後的版本，第一個區塊會被標成最後一個群組的欄名。
    original_levels = [list(lvl) for lvl in header_levels]
    columns = _labels(header_levels, n_cols)

    # 第一個區塊沒有分隔列，其基準名稱取自最內層表頭。
    # 不補的話，Table 2 的基準區塊列標題只是「Comprehensiveness」，
    # 與消融版本的「-High / Comprehensiveness」無從區分 ——
    # 實測問「消融版本的表現」時，模型會取用基準區塊的數字。
    aligned_all = [_assign_span(r["cells"], template, span=False) for r in body_rows]
    multi_block = any(
        not any(_NUMBER.search(c) for c in cells) and any(cells[1:]) for cells in aligned_all
    )
    inner = header_levels[-1] if header_levels else []
    base_group = inner[1] if multi_block and len(inner) > 1 and inner[1] else ""

    rows: list[tuple[str, dict[str, str]]] = []
    seen_labels: Counter = Counter()
    group = base_group
    for r in body_rows:
        aligned = _assign_span(r["cells"], template, span=False)
        if not any(_NUMBER.search(c) for c in aligned) and any(aligned[1:]):
            # 無數值的內文列是群組小標，取代最內層表頭並為後續列標上群組名。
            # 群組名取「這一列新出現、而前一層表頭沒有」的標籤：例如前一層是
            # NaiveRAG / LightRAG，這一列是 NaiveRAG / -High，群組名即 -High。
            prev = {v for v in (header_levels[-1] if header_levels else []) if v}
            group = " ".join(dict.fromkeys(v for v in aligned[1:] if v and v not in prev))
            spanned = _assign_span(r["cells"], template, span=True)
            header_levels = header_levels[:-1] + [spanned] if header_levels else [spanned]
            columns = _labels(header_levels, n_cols)
            continue
        label = f"{group} / {aligned[0]}" if group else aligned[0]
        seen_labels[label] += 1
        if seen_labels[label] > 1:      # 來源表格本身就有重複列標題時，加序號保住資料
            label = f"{label} #{seen_labels[label]}"
        rows.append((label, {columns[i - 1]: aligned[i] for i in range(1, n_cols) if aligned[i]}))

    if not rows:
        return None

    table = Table(
        table_id=tid, page=pno, caption=_caption(page, top, x0, x1),
        y0=top, y1=bottom, order=pdf.order_of(pno, top, x0), strategy=strategy,
        header_levels=header_levels, columns=columns, rows=rows,
        markdown=_markdown(original_levels, template, body_rows),
    )

    values = [v for _, vals in rows for v in vals.values()]
    numeric_ratio = sum(1 for v in values if _NUMBER.search(v)) / max(len(values), 1)
    mean_len = sum(len(v) for v in values) / max(len(values), 1)

    # 敘述型表格（案例研究、範例對照）的儲存格是整段文字而非可查的數值。
    # 只看數值比例不夠：長段文字裡常夾著年份或編號，比例會被拉高。
    # 加上平均字數判斷後，這類表格才會在驗證之前就被正確歸類 ——
    # 否則它們會以「驗證失敗」的形式計入統計，讓數字看起來像解析出了問題。
    if numeric_ratio < 0.3 or mean_len > 60:
        return _as_prose(table, "敘述型表格，採整表模式")

    table.validated, table.validation_note = _validate(page, x0, top, x1, bottom, table)
    if not table.validated:
        # 最後一道防線：寧可退化成整表原文，也不讓錯誤的儲存格進入索引。
        # 確定性查表必須是可信的，否則不如不提供。
        return _as_prose(table, f"低信心，退回整表模式（{table.validation_note}）", ok=False)
    return table


def _as_prose(table: Table, note: str, ok: bool = True) -> Table:
    table.kind = "prose"
    table.rows = []
    table.columns = []
    table.validated, table.validation_note = ok, note
    return table


def _grid(page, x0: float, y0: float, x1: float, y1: float) -> list[dict]:
    """把區域內的字元還原成列與儲存格，保留每個儲存格的 x 範圍。

    """
    words = [
        w for w in page.get_text("words")
        if w[1] >= y0 - 1 and w[3] <= y1 + 1 and w[0] >= x0 - 2 and w[2] <= x1 + 2
    ]
    if not words:
        return []


    buckets: dict[float, list] = {}
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        key = next((k for k in buckets if abs(k - w[1]) < _ROW_TOL), round(w[1], 1))
        buckets.setdefault(key, []).append(w)

    out = []
    for y in sorted(buckets):
        ws = sorted(buckets[y], key=lambda w: w[0])
        cells, cur = [], [ws[0]]
        for w in ws[1:]:
            if w[0] - cur[-1][2] > _COL_GAP:
                cells.append(cur)
                cur = [w]
            else:
                cur.append(w)
        cells.append(cur)
        out.append({
            "y": y,
            "cells": [(" ".join(c[4] for c in g), g[0][0], g[-1][2]) for g in cells],
        })
    return out


def _template(body_rows: list[dict], inner_v: list[Rule], x0: float, x1: float) -> list[tuple[float, float]]:
    """欄位邊界。有垂直線就用線，沒有就取最常見的欄數那一列當範本。"""
    if len(inner_v) >= 2:
        xs = sorted({round(r.x0, 1) for r in inner_v})
        xs = [x0] + xs + [x1]
        return [(xs[i], xs[i + 1]) for i in range(len(xs) - 1) if xs[i + 1] - xs[i] > 4]

    counts = Counter(len(r["cells"]) for r in body_rows)
    n = counts.most_common(1)[0][0]
    for r in body_rows:
        if len(r["cells"]) == n:
            return [(c[1], c[2]) for c in r["cells"]]
    return []


def _assign_span(
    cells: list[tuple[str, float, float]],
    template: list[tuple[float, float]],
    span: bool = False,
) -> list[str]:
    """把儲存格對應到欄位範本。

    span=True（表頭）：一個儲存格可以覆蓋多欄。先以 x 重疊配對，再用相鄰
    儲存格的中線補上未覆蓋的欄 —— 橫跨兩欄的表頭（例如 Agriculture 蓋住
    NaiveRAG 與 LightRAG）文字寬度往往只夠蓋到其中一欄，純靠重疊會漏掉另一欄。

    span=False（內文）：一個儲存格只能落在一欄。內文列的儲存格數少於欄數時，
    代表該列真的缺值，而不是有跨欄；若比照表頭做補位，同一個數值會被複製到
    相鄰欄位 —— 實測有四張表因此在守恆檢查中出現「多出」的重複數值。
    """
    out = [""] * len(template)
    if not cells:
        return out

    if not span:
        for text, cx0, cx1 in cells:
            overlaps = [
                (min(cx1, tx1) - max(cx0, tx0), i)
                for i, (tx0, tx1) in enumerate(template)
                if min(cx1, tx1) - max(cx0, tx0) > 0
            ]
            if overlaps:
                i = max(overlaps)[1]                      # 重疊最多的那一欄
            else:
                centre = (cx0 + cx1) / 2
                i = min(range(len(template)),
                        key=lambda j: abs(sum(template[j]) / 2 - centre))
            out[i] = f"{out[i]} {text}".strip() if out[i] else text
        return out

    for text, cx0, cx1 in cells:
        for i, (tx0, tx1) in enumerate(template):
            if min(cx1, tx1) - max(cx0, tx0) > 0:
                out[i] = f"{out[i]} {text}".strip() if out[i] else text

    # 涵蓋範圍只在左側設限：列標題欄位於表頭左方，不應被填入；
    # 右側則開放到底 —— 最後一個跨欄表頭（例如 Mix）必須延伸到最後一欄。
    lo = cells[0][1]
    bounds = [(cells[i][2] + cells[i + 1][1]) / 2 for i in range(len(cells) - 1)]
    for i, (tx0, tx1) in enumerate(template):
        if out[i]:
            continue
        centre = (tx0 + tx1) / 2
        if centre < lo:
            continue
        out[i] = cells[bisect_right(bounds, centre)][0]
    return out


def _labels(header_levels: list[list[str]], n_cols: int) -> list[str]:
    """把多層表頭合併成完整欄名，例如 Agriculture / NaiveRAG。

    欄名必須唯一：重複的鍵會讓後面的值覆寫前面的值，造成數值靜默消失。
    """
    out: list[str] = []
    seen: Counter = Counter()
    for i in range(1, n_cols):
        parts = [lvl[i] for lvl in header_levels if i < len(lvl) and lvl[i]]
        name = " / ".join(dict.fromkeys(parts)) or f"col{i}"
        seen[name] += 1
        out.append(name if seen[name] == 1 else f"{name} #{seen[name]}")
    return out


def _caption(page, top: float, x0: float, x1: float) -> str:
    """表格標號通常在上方（LaTeX 慣例），找不到再往下方找。"""
    for lo, hi in ((top - _CAPTION_WINDOW, top), (top, top + _CAPTION_WINDOW)):
        rect = pymupdf.Rect(x0 - 20, max(lo, 0), x1 + 20, hi)
        text = " ".join(page.get_text("text", clip=rect).split())
        hits = list(_CAPTION.finditer(text))
        if hits:
            m = hits[-1]      # 取最後一個：前面可能還黏著上一段正文
            body = " ".join(m.group(2).split())[:160]
            return f"Table {m.group(1)}: {body}".strip().rstrip(":")
    return ""


def _markdown(header_levels, template, body_rows) -> str:
    """整表渲染。每一列的標題都補上所屬區塊名稱。

    論文的表格常以分隔列切成多個區塊，各自對應不同的基準方法，
    而各區塊的列標題完全相同（都是 Comprehensiveness、Diversity…）。
    只靠分隔列標示區塊時，模型讀 20 列的表格會抓錯區塊 ——
    實測它把 NaiveRAG 區塊的數字當成 GraphRAG 的來回答。

    把區塊名稱補進每一列的標題後，每一列都能自己說明自己的歸屬。
    """
    lines = []
    for lvl in header_levels:
        lines.append("| " + " | ".join(lvl) + " |")
    lines.append("|" + "|".join([" --- "] * len(template)) + "|")

    aligned_rows = [_assign_span(r["cells"], template, span=False) for r in body_rows]
    multi_block = any(
        not any(_NUMBER.search(c) for c in cells) and any(cells[1:])
        for cells in aligned_rows
    )
    inner = header_levels[-1] if header_levels else []
    group = inner[1] if multi_block and len(inner) > 1 and inner[1] else ""

    prev_labels: set[str] = {v for lvl in header_levels for v in lvl if v}
    for r in body_rows:
        cells = _assign_span(r["cells"], template, span=False)
        if not any(_NUMBER.search(c) for c in cells) and any(cells[1:]):
            new = [v for v in cells[1:] if v and v not in prev_labels]
            group = new[0] if new else " ".join(dict.fromkeys(v for v in cells[1:] if v))
            prev_labels = {v for v in cells[1:] if v}
            lines.append("| " + " | ".join(cells) + " |")
            continue
        if group and cells[0]:
            cells = [f"{group} · {cells[0]}"] + cells[1:]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _validate(page, x0, y0, x1, y1, table: Table) -> tuple[bool, str]:
    """數值多重集守恆：區域內的數值與還原後儲存格的數值必須完全相同。

    這道檢查抓的是欄位對齊時掉字的情形 —— 那種錯誤不會拋例外，
    只會讓某個數字悄悄消失或跑到別欄。
    """
    region = Counter(
        _NUMBER.findall(" ".join(
            w[4] for w in page.get_text("words")
            if w[1] >= y0 - 1 and w[3] <= y1 + 1 and w[0] >= x0 - 2 and w[2] <= x1 + 2
        ))
    )
    # 跨欄表頭的同一段文字會被填進它涵蓋的每一欄，計數前需先去重，
    # 否則表頭中的數字會被重複計算，產生假的「多出」。
    header_texts = [t for lvl in table.header_levels for t in dict.fromkeys(x for x in lvl if x)]
    emitted = Counter(_NUMBER.findall(" ".join(
        [lbl.split(" #")[0] for lbl, _ in table.rows]
        + [v for _, vals in table.rows for v in vals.values()]
        + header_texts
    )))
    if region != emitted:
        missing, extra = region - emitted, emitted - region
        return False, f"數值不符：缺少 {dict(missing)} 多出 {dict(extra)}"

    # 守恆檢查看的是 rows（串列），但確定性查表用的是 cells（字典）。
    # 若列標題或欄名不唯一，字典會靜默覆寫，數值就此消失 —— 必須另外檢查。
    n_values = sum(len(v) for _, v in table.rows)
    if len(table.cells) != n_values:
        return False, f"鍵不唯一：{n_values} 個值只留下 {len(table.cells)} 個"
    return True, "數值守恆、鍵唯一"


def _pairwise_note(vals: dict[str, str]) -> str:
    """勝率表的每一列常是兩兩對比，同一組的兩個數字相加為 100%。

    不講明的話，模型會把「LightRAG 對 NaiveRAG 的 83.6%」拿去和
    「GraphRAG 對 LightRAG 的 48.4%」並列 —— 兩個數字各自都對，
    配在一起卻是錯的，而且看起來完全合理。實測四組配對全錯。

    因此在列尾直接寫明這是兩兩對比，讓這一列自己說明它的數字怎麼讀。
    """
    groups: dict[str, list[float]] = {}
    for col, val in vals.items():
        m = _PERCENT.fullmatch(val.strip())
        if not m:
            return ""
        groups.setdefault(col.split(" / ")[0], []).append(float(m.group(1)))
    pairs = [v for v in groups.values() if len(v) == 2]
    if not pairs or len(pairs) != len(groups):
        return ""
    if not all(abs(sum(v) - 100) < 0.6 for v in pairs):
        return ""
    return "PAIRWISE"


def is_pairwise(table: Table) -> bool:
    """整張表的每一列都是兩兩對比（同組兩數相加為 100%）。

    註記放在整表開頭一次，不放在每一列末尾 —— 實測放在 16 列的行尾時，
    模型照樣把「LightRAG 對 NaiveRAG 的 83.6%」與「GraphRAG 的 48.4%」並列。
    埋在長列尾端的限制等於沒有寫，而且重複 16 次還多花 token。
    """
    return bool(table.rows) and all(_pairwise_note(v) for _, v in table.rows)


PAIRWISE_WARNING = (
    "⚠ 本表為兩兩對比：每一列只描述 LightRAG 與該列指定的**單一**對手，"
    "同一資料集內的兩個數值相加為 100%。"
    "比較兩個方法時，兩個數字必須取自**同一列** —— "
    "不可把「對 A 那一列的數值」與「對 B 那一列的數值」並列。"
)


def linearize(table: Table) -> list[str]:
    """逐列轉成「欄名 = 值」的自然語言，讓關鍵字與語意檢索都能命中。"""
    head = f"【{table.caption or table.table_id} · p.{table.page}】"
    return [
        f"{head} {label}：" + "；".join(f"{col} = {val}" for col, val in vals.items())
        for label, vals in table.rows
    ]
