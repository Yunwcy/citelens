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
  onDelete: (id: string) => void;
};

export function DocumentPanel({
  documents, activeId, job, error, t, onSelect, onUpload, onUploadUrl, onDelete,
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const busy = job !== null && job.stage !== "ready" && job.stage !== "failed";

  return (
    <aside className="w-60 shrink-0 flex flex-col gap-2 p-3 border-r border-line">
      <h2 className="text-xs text-ink-soft px-1">{t.documents}</h2>

      {documents.map((d) => {
        const active = d.doc_id === activeId;
        const asking = confirming === d.doc_id;
        return (
          // 卡片本身是按鈕，刪除鈕不能巢狀其中 —— 改為絕對定位的同層元素
          <div key={d.doc_id} className="relative group">
            <button
              onClick={() => onSelect(d.doc_id)}
              className={`w-full text-left rounded-lg border p-2.5 transition-colors ${
                active ? "border-accent bg-accent-soft"
                       : "border-line bg-surface hover:border-line-strong"
              }`}
            >
              <div className="truncate font-medium pr-5">{d.filename}</div>
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

            {!asking && (
              <button
                onClick={() => setConfirming(d.doc_id)}
                aria-label={t.deleteDoc}
                title={t.deleteDoc}
                className="absolute right-1.5 top-1.5 h-5 w-5 rounded text-ink-faint
                           opacity-0 group-hover:opacity-100 focus:opacity-100
                           hover:bg-line hover:text-ink transition-opacity"
              >
                ×
              </button>
            )}

            {/* 刪除不可復原，因此改為兩段式確認，而不是點一下就消失 */}
            {asking && (
              <div className="absolute inset-0 flex flex-col justify-center gap-1.5
                              rounded-lg border border-red-300 bg-red-50 px-2.5">
                <span className="text-xs text-red-800 leading-snug">{t.confirmDelete}</span>
                <div className="flex gap-1.5">
                  <button
                    onClick={() => { setConfirming(null); onDelete(d.doc_id); }}
                    className="rounded border border-red-300 bg-white px-2 py-0.5
                               text-xs text-red-800 hover:bg-red-100"
                  >
                    {t.deleteDoc}
                  </button>
                  <button
                    onClick={() => setConfirming(null)}
                    className="rounded border border-line bg-white px-2 py-0.5 text-xs"
                  >
                    {t.cancel}
                  </button>
                </div>
              </div>
            )}
          </div>
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
