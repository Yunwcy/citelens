import { useRef } from "react";
import type { DocSummary } from "../api";

const STAGE_LABEL: Record<string, string> = {
  queued: "排隊中", parsing: "解析中", indexing: "建立索引",
  summarizing: "摘要準備中", ready: "就緒", failed: "無法解析",
};

type Props = {
  documents: DocSummary[];
  activeId: string | null;
  stage: string | null;
  error: string | null;
  onSelect: (id: string) => void;
  onUpload: (file: File) => void;
};

export function DocumentPanel({ documents, activeId, stage, error, onSelect, onUpload }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const busy = stage !== null && stage !== "ready" && stage !== "failed";

  return (
    <aside className="w-60 shrink-0 flex flex-col gap-2 p-3 border-r border-line">
      <h2 className="text-xs text-ink-soft px-1">文件</h2>

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
              {d.pages} 頁 · {d.chunks} 個片段
            </div>
            <div className="mt-1.5 flex items-center gap-1.5">
              <Badge stage={active && busy ? stage! : "ready"} />
              {d.tables ? <span className="text-xs text-ink-faint">{d.tables} 張表</span> : null}
            </div>
          </button>
        );
      })}

      {busy && !documents.some((d) => d.doc_id === activeId) && (
        <div className="rounded-lg border border-line bg-surface p-2.5">
          <div className="truncate font-medium text-ink-soft">處理中</div>
          <div className="mt-1.5"><Badge stage={stage!} /></div>
        </div>
      )}

      <button
        onClick={() => input.current?.click()}
        className="rounded-lg border border-dashed border-line-strong p-4 text-center
                   hover:border-accent hover:bg-accent-soft/40 transition-colors"
      >
        <div>拖曳 PDF 到這裡</div>
        <div className="text-xs text-ink-soft mt-0.5">PDF · 最大 30MB</div>
      </button>
      <input
        ref={input} type="file" accept="application/pdf" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = ""; }}
      />

      {error && <p className="text-xs text-red-700 px-1 leading-relaxed">{error}</p>}
    </aside>
  );
}

function Badge({ stage }: { stage: string }) {
  const ready = stage === "ready";
  const failed = stage === "failed";
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs ${
      failed ? "bg-red-50 text-red-800"
        : ready ? "bg-accent-soft text-accent-deep"
        : "bg-amber-50 text-amber-800"
    }`}>
      {STAGE_LABEL[stage] ?? stage}
    </span>
  );
}
