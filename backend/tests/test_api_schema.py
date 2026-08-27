"""API 文件的完整性。

自動生成的文件只會忠實反映標了多少型別 —— 沒標的地方它不會警告，
只會安靜地印一個 additionalProp1 佔位符。這項測試把那件事變成會失敗的斷言。
"""
from fastapi.testclient import TestClient

from app.main import app


def _schemas():
    with TestClient(app) as c:
        return c.get("/openapi.json").json()


def test_每支端點的回應都要有可讀的形狀():
    spec = _schemas()
    vague: list[str] = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            ok = {k: v for k, v in op.get("responses", {}).items() if k.startswith("2")}
            if not ok:
                vague.append(f"{method.upper()} {path}：沒有成功回應")
                continue
            content = ok[sorted(ok)[0]].get("content", {})
            if not content:
                vague.append(f"{method.upper()} {path}：未宣告回應內容")
                continue
            media = next(iter(content))
            if media != "application/json":
                continue                      # 串流與純文字已明確標示媒體型別
            sch = content[media].get("schema", {})
            ref = sch.get("$ref") or sch.get("items", {}).get("$ref")
            if not ref and not sch.get("properties"):
                vague.append(f"{method.upper()} {path}：回應是無欄位的 object")
    assert not vague, "以下端點的 API 文件只會顯示佔位符：\n" + "\n".join(vague)


def test_串流端點不可宣告為_json():
    """宣告成 JSON 但實際回 SSE，比沒有文件更誤導。"""
    spec = _schemas()
    for path in ("/api/query", "/api/jobs/{job_id}/events"):
        op = next(iter(spec["paths"][path].values()))
        media = next(iter(op["responses"]["200"]["content"]))
        assert media == "text/event-stream", f"{path} 宣告為 {media}"
