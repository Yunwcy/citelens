import { useEffect, useRef, useState } from "react";
import type { Debug, Source } from "../api";

export type Turn = {
  question: string;
  answer: string;
  sources: Source[];
  debug: Debug | null;
  stage: string | null;
};

const STAGE_LABEL: Record<string, string> = {
  retrieving: "檢索中…", packing: "整理片段…", generating: "產生回答…",
};

const ROUTE_LABEL: Record<string, string> = {
  summary: "摘要", comparison: "比較", table_lookup: "表格查詢", qa: "一般問答",
};

type Props = {
  turns: Turn[];
  quickQuestions: string[];
  ready: boolean;
  busy: boolean;
  onAsk: (q: string) => void;
  onCite: (n: number) => void;
};

export function Chat({ turns, quickQuestions, ready, busy, onAsk, onCite }: Props) {
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const send = (q: string) => {
    const value = q.trim();
    if (!value || !ready || busy) return;
    setText("");
    onAsk(value);
  };

  return (
    <section className="flex-1 min-w-0 flex flex-col">
      <h2 className="text-xs text-ink-soft px-4 pt-3 pb-1">對話</h2>

      <div className="flex-1 overflow-y-auto px-4 pb-2">
        {turns.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center gap-1">
            <p className="text-base">{ready ? "開始提問" : "先上傳一份文件"}</p>
            <p className="text-ink-soft">
              {ready ? "回答會附上頁碼與章節出處" : "接著就能針對內容提問，回答會附上頁碼與章節出處"}
            </p>
          </div>
        )}

        {turns.map((t, i) => (
          <div key={i} className="py-3 flex flex-col gap-2.5">
            <div className="self-end max-w-[80%] rounded-lg bg-accent-soft text-accent-deep px-3 py-2">
              {t.question}
            </div>

            {t.stage && !t.answer && (
              <div className="text-ink-soft">{STAGE_LABEL[t.stage] ?? t.stage}</div>
            )}

            {t.answer && (
              <div className="max-w-[92%] whitespace-pre-wrap leading-relaxed">
                <Cited text={t.answer} onCite={onCite} />
              </div>
            )}

            {t.debug && <DebugPanel debug={t.debug} />}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <div className="px-4 pb-3 flex flex-col gap-2">
        {quickQuestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {quickQuestions.map((q) => (
              <button
                key={q}
                onClick={() => send(q)}
                disabled={!ready || busy}
                className="rounded-full border border-line px-3 py-1 text-xs bg-surface
                           hover:border-accent hover:text-accent-deep disabled:opacity-40
                           disabled:hover:border-line transition-colors"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        <div className="flex gap-1.5">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send(text)}
            disabled={!ready}
            placeholder="針對這份文件提問"
            className="flex-1 rounded-lg border border-line bg-surface px-3 py-2
                       outline-none focus:border-accent disabled:bg-surface-sunk"
          />
          <button
            onClick={() => send(text)}
            disabled={!ready || busy || !text.trim()}
            className="rounded-lg bg-accent px-4 text-white disabled:opacity-40"
          >
            {busy ? "回答中…" : "送出"}
          </button>
        </div>
      </div>
    </section>
  );
}

/** 把答案中的 [1]、[2] 變成可點擊的引用標記。 */
function Cited({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <>
      {parts.map((p, i) => {
        const m = p.match(/^\[(\d+)\]$/);
        if (!m) return <span key={i}>{p}</span>;
        return (
          <button
            key={i}
            onClick={() => onCite(Number(m[1]))}
            className="text-accent hover:underline align-baseline"
          >
            {p}
          </button>
        );
      })}
    </>
  );
}

function DebugPanel({ debug }: { debug: Debug }) {
  const [open, setOpen] = useState(false);
  const pct = Math.round((debug.context_tokens / debug.context_budget) * 100);

  return (
    <div className="border-t border-line pt-2">
      <button onClick={() => setOpen(!open)} className="text-xs text-ink-soft hover:text-ink">
        {open ? "▾" : "▸"} 檢索細節
      </button>
      {open && (
        <dl className="mt-1.5 grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-ink-soft">路由</dt>
          <dd>
            {ROUTE_LABEL[debug.route] ?? debug.route}
            {debug.route_reason !== ROUTE_LABEL[debug.route] && (
              <span className="text-ink-faint"> · {debug.route_reason}</span>
            )}
            {debug.entities.length > 0 && (
              <span className="text-ink-faint"> · {debug.entities.join(" / ")}</span>
            )}
          </dd>

          <dt className="text-ink-soft">片段</dt>
          <dd>
            取回 {debug.retrieved} 個，採用 {debug.packed} 個
            {debug.dropped.length > 0 && (
              <span className="text-amber-800"> · 因預算不足捨棄 {debug.dropped.length} 個</span>
            )}
          </dd>

          <dt className="text-ink-soft">內容量</dt>
          <dd>
            {debug.context_tokens.toLocaleString()} / {debug.context_budget.toLocaleString()} tokens
            <span className="text-ink-faint"> （{pct}%）</span>
          </dd>

          <dt className="text-ink-soft">用量</dt>
          <dd>
            {debug.prompt_tokens.toLocaleString()} 進 / {debug.completion_tokens.toLocaleString()} 出
            <span className="text-ink-faint"> · US${debug.cost_usd.toFixed(6)}</span>
          </dd>

          <dt className="text-ink-soft">耗時</dt>
          <dd>
            檢索 {Math.round(debug.retrieval_ms)} ms · 生成 {Math.round(debug.llm_ms)} ms
          </dd>
        </dl>
      )}
    </div>
  );
}
