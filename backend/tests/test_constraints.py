"""作業限制的結構性檢查。

題目給了兩條硬限制：

1. 不能使用現成的 gpt/gemini API 解析文件，LLM 只做 text-to-text
2. 假定 LLM max context size 為 10K

第 2 條由 test_budget.py 守著（提示詞實際大小、最壞情況的總量）。
這裡守第 1 條 —— 它比較容易在不知不覺間被違反：把 PDF 直接丟給模型的
「省事」寫法、多模態輸入欄位、或是為了省磁碟改用外部向量化服務，
都不會讓任何既有測試變紅，但都會違反限制。

所以把「哪些檔案可以碰外部模型」與「碰的時候只能傳字串」寫成會失敗的檢查。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

# 唯一允許連到外部模型服務的檔案。解析、切塊、向量化、檢索都不在此列。
ALLOWED = {"llm/client.py"}

# 現成的「幫你讀文件」型 API，正是題目禁止的那一類
FORBIDDEN_APIS = (
    "files.create", "assistants", "vector_stores",   # OpenAI 檔案／助理
    "responses.create",                              # 可直接吃 PDF
    "generativeai", "genai", "gemini",               # Gemini
    "input_file", "image_url", "input_image",        # 多模態輸入欄位
)


def _code_only(src: str) -> str:
    """去掉註解與 docstring，只留會執行的程式碼。

    不這樣做的話，這份測試會被說明文字絆倒 —— 檔案裡寫一句
    「本專案不使用 gemini」就會被自己判定為違規。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str) and len(body) > 1):
                del body[0]
    return ast.unparse(tree)


def _sources() -> list[tuple[str, str]]:
    # 金鑰欄位的名字本身不算「觸及外部服務」—— 它只是一個設定項，
    # 由 client.py 取用。先拿掉，才不會讓 config.py 誤判。
    return [
        (str(p.relative_to(APP)),
         _code_only(p.read_text(encoding="utf-8")).replace("openai_api_key", "_key"))
        for p in APP.rglob("*.py")
    ]


def test_只有一個檔案能連到外部模型服務():
    offenders = sorted(
        rel for rel, src in _sources()
        if rel not in ALLOWED and ("openai" in src.lower() or "genai" in src.lower())
    )
    assert not offenders, (
        f"這些檔案觸及外部模型服務：{offenders}。"
        f"解析與向量化必須全部在本地完成 —— 只有 {ALLOWED} 可以呼叫模型"
    )


def test_沒有使用任何現成的文件解析型_API():
    hits = sorted(
        f"{rel}：{api}"
        for rel, src in _sources()
        for api in FORBIDDEN_APIS
        if api in src.lower()
    )
    assert not hits, f"用到了題目禁止的文件解析型 API：{hits}"


def test_模型介面只收字串只回字串():
    """text-to-text 這條限制要成立，介面本身就不能有非文字的入口。"""
    from app.llm import client

    # max_tokens 是輸出長度上限、meta 是回報 finish_reason 的容器，
    # 兩者都不會進到模型看到的內容裡。其餘參數一律必須是字串。
    not_content = {"max_tokens", "meta"}

    for fn in (client.generate, client.generate_stream):
        sig = inspect.signature(fn)
        params = [n for n in sig.parameters if n not in not_content]
        assert params, f"{fn.__name__} 沒有任何內容參數，簽章可能被改過"
        for name in params:
            ann = str(sig.parameters[name].annotation)
            assert "str" in ann, f"{fn.__name__} 的參數 {name} 型別是 {ann}，不是字串"


def test_送進模型的訊息內容都是純文字():
    """OpenAI 的 messages 允許 content 是列表，用來夾帶圖片或檔案。
    這裡確認本專案組出來的 content 一律是字串，而不是那種列表結構。
    """
    tree = ast.parse((APP / "llm" / "client.py").read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
        if "content" not in keys:
            continue
        checked += 1
        value = node.values[keys.index("content")]
        assert not isinstance(value, (ast.List, ast.Tuple)), (
            "訊息的 content 是列表 —— 那是多模態輸入的形狀，"
            "違反「LLM 只做 text-to-text」"
        )
    assert checked >= 2, "沒找到組訊息的地方，這個測試可能已經失效"


def test_向量化沒有外部後端可選():
    """曾經有一個 openai 向量化後端，預設不啟用。
    但預設值不是保證 —— 一個環境變數就能讓文件內容離開本機。
    """
    from app.config import Settings

    options = getattr(Settings.model_fields["embedding_backend"].annotation,
                      "__args__", ())
    assert options, "抓不到向量化後端的選項清單"
    assert all("openai" not in str(o) and "gemini" not in str(o) for o in options), (
        f"向量化後端仍可切換到外部服務：{options}"
    )
