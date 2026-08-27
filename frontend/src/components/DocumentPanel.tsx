import { useRef, useState } from "react";
import type { DocSummary } from "../api";
import type { Strings } from "../i18n";

export type Job = {
  docId: string;
  filename: string;
  stage: string;
  pages?: number;
  chunks?: number;
  tables?: number;
};

type Props = {
  documents: DocSummary[];
  activeId: string | null;
  job: Job | null;
  error: string | null;
  t: Strings;
  onSelect: (id: string) => void;
  onUpload: (file: File) => void;
  onUploadUrl: (url: string) => void;
};

export function DocumentPanel({
  documents, activeId, job, error, t, onSelect, onUpload, onUploadUrl,
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const busy = job !== null && job.stage !== "ready" && job.stage !== "failed";

  return (
    <aside className="w-60 shrink-0 flex flex-col gap-2 p-3 border-r border-line">
      <h2 className="text-xs text-ink-soft px-1">{t.documents}</h2>

      {documents.map((d) => {
        const active = d.doc_id === activeId;
        return (
          <button
            key={d.doc_id}
            onClick={() => onSelect(d.doc_id)}
            className={`text-left rounded-lg border p-2.5 transition-colors ${
              active ? "border-accent bg-accent-soft" : "border-line bg-surface hover:border-line-strong"
            }`}
          >
            <div className="truncate font-medium">{d.filename}</div>
            <div className="text-xs text-ink-soft mt-0.5">
              {t.pages(d.pages ?? 0)} · {t.passages(d.chunks ?? 0)}
            </div>
            <div className="mt-1.5 flex items-center gap-1.5">
              <Badge stage="ready" t={t} />
              {d.tables ? (
                <span className="text-xs text-ink-faint">{t.tables(d.tables)}</span>
              ) : null}
            </div>
          </button>
        );
      })}

      {/* 處理中的卡片獨立於選取狀態顯示。先前綁在 activeId 上，
          使用者在處理期間點了別份文件，這張卡片就會消失，看起來像檔案不見了。 */}
      {busy && job && !documents.some((d) => d.doc_id === job.docId) && (
        <div className="rounded-lg border border-line bg-surface p-2.5">
          <div className="truncate font-medium text-ink-soft">{job.filename}</div>
          {job.pages ? (
            <div className="mt-0.5 text-xs text-ink-soft">
              {t.pages(job.pages)}
              {job.chunks ? ` · ${t.passages(job.chunks)}` : ""}
              {job.tables ? ` · ${t.tables(job.tables)}` : ""}
            </div>
          ) : null}
          <div className="mt-1.5 flex flex-col gap-1">
            <Badge stage={job.stage} t={t} />
            <span className="text-xs text-ink-faint">{t.stillProcessing}</span>
          </div>
        </div>
      )}

      <button
        onClick={() => input.current?.click()}
        className="rounded-lg border border-dashed border-line-strong p-4 text-center
                   hover:border-accent hover:bg-accent-soft/40 transition-colors"
      >
        <div>{t.dropPdf}</div>
        <div className="text-xs text-ink-soft mt-0.5">{t.pdfLimit}</div>
      </button>
      <input
        ref={input} type="file" accept="application/pdf" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = ""; }}
      />

      <div className="flex flex-col gap-1">
        <label className="text-xs text-ink-soft px-1">{t.orPasteLink}</label>
        <div className="flex gap-1">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && url.trim()) { onUploadUrl(url.trim()); setUrl(""); }
            }}
            disabled={busy}
            placeholder={t.linkPlaceholder}
            className="flex-1 min-w-0 rounded-lg border border-line bg-surface px-2 py-1.5
                       text-xs outline-none focus:border-accent disabled:bg-surface-sunk"
          />
          <button
            onClick={() => { if (url.trim()) { onUploadUrl(url.trim()); setUrl(""); } }}
            disabled={busy || !url.trim()}
            className="rounded-lg border border-line px-2 text-xs disabled:opacity-40
                       hover:border-accent hover:text-accent-deep transition-colors"
          >
            {t.import}
          </button>
        </div>
      </div>

      {error && <p className="text-xs text-red-700 px-1 leading-relaxed">{error}</p>}
    </aside>
  );
}

function Badge({ stage, t }: { stage: string; t: Strings }) {
  const ready = stage === "ready";
  const failed = stage === "failed";
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs ${
      failed ? "bg-red-50 text-red-800"
        : ready ? "bg-accent-soft text-accent-deep"
        : "bg-amber-50 text-amber-800"
    }`}>
      {t.stage[stage] ?? stage}
    </span>
  );
}
