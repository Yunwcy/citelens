import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { Chat, type Turn } from "./components/Chat";
import { DocumentPanel, type Job } from "./components/DocumentPanel";
import { SourcePanel } from "./components/SourcePanel";

export default function App() {
  const [documents, setDocuments] = useState<api.DocSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  // 進行中的索引任務獨立於選取狀態：使用者在處理期間切換文件時，
  // 任務仍要繼續追蹤，卡片也不該消失。
  const [job, setJob] = useState<Job | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quick, setQuick] = useState<string[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const docs = await api.listDocuments();
    setDocuments(docs);
    return docs;
  }, []);

  useEffect(() => {
    refresh().then((docs) => {
      if (docs.length && !activeId) select(docs[0].doc_id);
    }).catch((e) => setError(String(e.message ?? e)));
  }, []);

  async function select(id: string) {
    setActiveId(id);
    setTurns([]);
    setReady(false);
    try {
      const doc = await api.getDocument(id);
      setQuick(doc.quick_questions);
      setReady(true);
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  }

  const handleUpload = (file: File) => start(() => api.upload(file), file.name);
  const handleUploadUrl = (url: string) => start(() => api.uploadFromUrl(url), "由網址匯入");

  async function start(
    begin: () => Promise<{ job_id: string; doc_id: string }>,
    label: string,
  ) {
    setError(null);
    try {
      const { job_id, doc_id } = await begin();
      setJob({ docId: doc_id, filename: label, stage: "queued" });

      for await (const ev of api.jobEvents(job_id)) {
        setJob((j) =>
          j && j.docId === doc_id
            ? { ...j, stage: ev.stage, pages: ev.pages, chunks: ev.chunks, tables: ev.tables }
            : j,
        );
        if (ev.error) { setError(ev.error); break; }
        if (ev.stage === "ready") {
          await refresh();
          setJob(null);
          // 只有在使用者沒有切走時才自動開啟，避免打斷正在進行的對話
          setActiveId((cur) => {
            if (cur === null || cur === doc_id) { void select(doc_id); return doc_id; }
            return cur;
          });
          break;
        }
      }
    } catch (e: any) {
      setJob((j) => (j ? { ...j, stage: "failed" } : j));
      setError(e.message ?? String(e));
    }
  }

  async function ask(question: string) {
    if (!activeId) return;
    setBusy(true);
    setTurns((t) => [...t, { question, answer: "", sources: [], debug: null, stage: null }]);

    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((all) => all.map((t, i) => (i === all.length - 1 ? fn(t) : t)));

    try {
      for await (const ev of api.askStream(activeId, question)) {
        if (ev.type === "stage") patch((t) => ({ ...t, stage: ev.stage }));
        else if (ev.type === "token") patch((t) => ({ ...t, answer: t.answer + ev.text }));
        else if (ev.type === "done")
          patch((t) => ({ ...t, sources: ev.sources, debug: ev.debug, stage: null }));
      }
    } catch (e: any) {
      patch((t) => ({ ...t, answer: t.answer || `回答失敗：${e.message ?? e}`, stage: null }));
    } finally {
      setBusy(false);
    }
  }

  const scrollToSource = (n: number) => {
    document.getElementById(`src-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const sources = turns.length ? turns[turns.length - 1].sources : [];
  const canAsk = ready && !!activeId;

  return (
    <div className="h-full flex flex-col bg-surface">
      <header className="flex items-baseline gap-2.5 px-4 py-2.5 border-b border-line">
        <h1 className="text-base font-medium">CiteLens</h1>
        <span className="text-xs text-ink-soft">文件問答 · 答案附出處</span>
      </header>

      <div className="flex-1 min-h-0 flex">
        <DocumentPanel
          documents={documents} activeId={activeId} job={job} error={error}
          onSelect={select} onUpload={handleUpload} onUploadUrl={handleUploadUrl}
        />
        <Chat
          turns={turns} quickQuestions={quick} ready={canAsk} busy={busy}
          onAsk={ask} onCite={scrollToSource}
        />
        <SourcePanel sources={sources} />
      </div>
    </div>
  );
}
