import { useEffect, useRef, useState } from "react";
import type { Debug, Source } from "../api";
import type { Strings } from "../i18n";
import { Markdown } from "./Markdown";

export type Turn = {
  question: string;
  answer: string;
  sources: Source[];
  debug: Debug | null;
  stage: string | null;
  /** 摘要的 map-reduce 進度；一般問答為 null。 */
  progress: { phase: string; done: number; total: number } | null;
  /** 模型判定文件未涵蓋此問題。 */
  declined: boolean;
};

type Props = {
  turns: Turn[];
  quickQuestions: string[];
  ready: boolean;
  busy: boolean;
  t: Strings;
  onAsk: (q: string) => void;
  onCite: (n: number) => void;
};

export function Chat({ turns, quickQuestions, ready, busy, t, onAsk, onCite }: Props) {
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
      <h2 className="text-xs text-ink-soft px-4 pt-3 pb-1">{t.chat}</h2>

      <div className="flex-1 overflow-y-auto px-4 pb-2">
        {turns.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center gap-1">
            <p className="text-base">{ready ? t.emptyTitleReady : t.emptyTitleNoDoc}</p>
            <p className="text-ink-soft">
              {ready ? t.emptyBodyReady : t.emptyBodyNoDoc}
            </p>
          </div>
        )}

        {turns.map((turn, i) => (
          <div key={i} className="py-3 flex flex-col gap-2.5">
            <div className="self-end max-w-[80%] rounded-lg bg-accent-soft text-accent-deep px-3 py-2">
              {turn.question}
            </div>

            {turn.stage && !turn.answer && (
              <Waiting turn={turn} t={t} />
            )}

            {turn.answer && (
              <div className="max-w-[92%] leading-relaxed">
                {turn.declined && (
                  <p className="mb-2 inline-flex items-center gap-1.5 rounded-lg border
                                border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900">
                    <span aria-hidden="true">◍</span>{t.declinedBadge}
                  </p>
                )}
                <Markdown text={turn.answer} onCite={onCite} />
              </div>
            )}

            {turn.debug && <DebugPanel debug={turn.debug} t={t} />}
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
            placeholder={t.askPlaceholder}
            className="flex-1 rounded-lg border border-line bg-surface px-3 py-2
                       outline-none focus:border-accent disabled:bg-surface-sunk"
          />
          <button
            onClick={() => send(text)}
            disabled={!ready || busy || !text.trim()}
            className="rounded-lg bg-accent px-4 text-white disabled:opacity-40"
          >
            {busy ? t.answering : t.send}
          </button>
        </div>
      </div>
    </section>
  );
}

/** 等待期間顯示目前階段。摘要另外顯示 map-reduce 的實際進度 ——
 *  未快取時要跑十幾次模型呼叫，沒有進度就只是一片空白。 */
function Waiting({ turn, t }: { turn: Turn; t: Strings }) {
  const p = turn.progress;
  if (p) {
    const step = p.phase === "map" ? 1 : 2;
    const pct = p.total ? (p.done / p.total) * 100 : 0;
    return (
      <div className="flex flex-col gap-1.5 max-w-[22rem]">
        <span className="text-ink-soft text-xs">
          {t.summaryPhase(step, t.summaryStepLabel[p.phase] ?? p.phase, p.done, p.total)}
        </span>
        <div className="h-1 rounded-full bg-line overflow-hidden">
          <div className="h-full bg-accent transition-[width] duration-300"
               style={{ width: `${Math.max(pct, 4)}%` }} />
        </div>
      </div>
    );
  }
  return <div className="text-ink-soft">{t.streamStage[turn.stage!] ?? turn.stage}</div>;
}

function DebugPanel({ debug, t }: { debug: Debug; t: Strings }) {
  const [open, setOpen] = useState(false);
  const pct = Math.round((debug.context_tokens / debug.context_budget) * 100);

  return (
    <div className="border-t border-line pt-2">
      <button onClick={() => setOpen(!open)} className="text-xs text-ink-soft hover:text-ink">
        {open ? "▾" : "▸"} {t.details}
        <span className="text-ink-faint"> · {t.stripMode[debug.route] ?? debug.route}</span>
      </button>
      {open && (
        <dl className="mt-1.5 grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-ink-soft">{t.route}</dt>
          <dd>
            {t.routeLabel[debug.route] ?? debug.route}
            {debug.route_reason !== t.routeLabel[debug.route] && (
              <span className="text-ink-faint"> · {debug.route_reason}</span>
            )}
            {debug.entities.length > 0 && (
              <span className="text-ink-faint"> · {debug.entities.join(" / ")}</span>
            )}
          </dd>

          <dt className="text-ink-soft">{t.passagesLabel}</dt>
          <dd>
            {t.retrievedUsed(debug.retrieved, debug.packed)}
            {debug.dropped.length > 0 && (
              <span className="text-amber-800">{t.droppedNote(debug.dropped.length)}</span>
            )}
          </dd>

          <dt className="text-ink-soft">{t.contextLabel}</dt>
          <dd>
            {debug.context_tokens.toLocaleString()} / {debug.context_budget.toLocaleString()} tokens
            <span className="text-ink-faint"> （{pct}%）</span>
          </dd>

          <dt className="text-ink-soft">{t.usage}</dt>
          <dd>
            {t.tokensInOut(debug.prompt_tokens.toLocaleString(),
                           debug.completion_tokens.toLocaleString())}
            <span className="text-ink-faint"> · US${debug.cost_usd.toFixed(6)}</span>
          </dd>

          <dt className="text-ink-soft">{t.latency}</dt>
          <dd>
            {t.latencyValue(Math.round(debug.retrieval_ms), Math.round(debug.llm_ms))}
          </dd>
        </dl>
      )}
    </div>
  );
}
