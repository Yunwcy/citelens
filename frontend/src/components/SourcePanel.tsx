import { useState } from "react";
import type { Source } from "../api";
import type { Strings } from "../i18n";

export function SourcePanel({ sources, t }: { sources: Source[]; t: Strings }) {
  const [open, setOpen] = useState<number | null>(null);
  // 模型未標註引用時，這些片段只是「送進去的依據」，不是答案指名的來源。
  // 標題必須據實區分，否則使用者會以為答案裡找得到對應的標記。
  const cited = sources.length === 0 || sources[0].cited;

  return (
    <aside className="w-72 shrink-0 flex flex-col gap-2 p-3 border-l border-line overflow-y-auto">
      <h2 className="text-xs text-ink-soft px-1">{cited ? t.sources : t.retrievedPassages}</h2>
      {!cited && (
        <p className="rounded-lg bg-amber-50 px-2 py-1.5 text-xs leading-relaxed text-amber-800">
          {t.noCitationNote}
        </p>
      )}

      {sources.length === 0 && (
        <p className="text-xs text-ink-faint px-1 leading-relaxed">
          {t.sourcesEmpty}
        </p>
      )}

      {sources.map((s) => (
        <div key={s.n} id={`src-${s.n}`} className="rounded-lg border border-line bg-surface">
          <button
            onClick={() => setOpen(open === s.n ? null : s.n)}
            className="w-full text-left p-2.5"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-accent font-medium">[{s.n}]</span>
              <span className="text-xs text-ink-faint">
                {s.kind === "summary" ? t.summaryTag : s.score.toFixed(4)}
              </span>
            </div>
            <div className="mt-0.5">
              {t.page(s.page)}
              {s.kind === "table_row" || s.kind === "table_full"
                ? <span className="text-ink-soft"> · {t.tableTag}</span> : null}
            </div>
            {s.section && <div className="text-xs text-ink-soft mt-0.5">{s.section}</div>}
          </button>
          {open === s.n && (
            <pre className={`px-2.5 pb-2.5 text-xs text-ink-soft whitespace-pre-wrap break-words
                             leading-relaxed ${s.kind !== "text" ? "font-mono" : ""}`}>
              {s.text}
            </pre>
          )}
        </div>
      ))}

      {sources.length > 0 && (
        <p className="text-xs text-ink-faint px-1">{t.clickToExpand}</p>
      )}
    </aside>
  );
}
