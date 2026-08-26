import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { Chat, type Turn } from "./components/Chat";
import { DocumentPanel } from "./components/DocumentPanel";
import { SourcePanel } from "./components/SourcePanel";

export default function App() {
  const [documents, setDocuments] = useState<api.DocSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stage, setStage] = useState<string | null>(null);
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
    setStage("ready");
    try {
      const doc = await api.getDocument(id);
      setQuick(doc.quick_questions);
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  }

  const handleUpload = (file: File) => start(() => api.upload(file));
  const handleUploadUrl = (url: string) => start(() => api.uploadFromUrl(url));

  async function start(begin: () => Promise<{ job_id: string; doc_id: string }>) {
    setError(null);
    setTurns([]);
    setQuick([]);
    try {
      const { job_id, doc_id } = await begin();
      setActiveId(doc_id);
      setStage("queued");
      for await (const ev of api.jobEvents(job_id)) {
        setStage(ev.stage);
        if (ev.error) { setError(ev.error); break; }
        if (ev.stage === "ready") { await refresh(); await select(doc_id); break; }
      }
    } catch (e: any) {
      setStage("failed");
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
  const ready = stage === "ready" && !!activeId;

  return (
    <div className="h-full flex flex-col bg-surface">
      <header className="flex items-baseline gap-2.5 px-4 py-2.5 border-b border-line">
        <h1 className="text-base font-medium">CiteLens</h1>
        <span className="text-xs text-ink-soft">文件問答 · 答案附出處</span>
      </header>

      <div className="flex-1 min-h-0 flex">
        <DocumentPanel
          documents={documents} activeId={activeId} stage={stage} error={error}
          onSelect={select} onUpload={handleUpload} onUploadUrl={handleUploadUrl}
        />
        <Chat
          turns={turns} quickQuestions={quick} ready={ready} busy={busy}
          onAsk={ask} onCite={scrollToSource}
        />
        <SourcePanel sources={sources} />
      </div>
    </div>
  );
}
