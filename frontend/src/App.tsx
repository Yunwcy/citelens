import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { STRINGS, detectLang, type Lang } from "./i18n";
import { Chat, type Turn } from "./components/Chat";
import { DocumentPanel, type Job } from "./components/DocumentPanel";
import { SourcePanel } from "./components/SourcePanel";

const LANG_KEY = "citelens.lang";

export default function App() {
  const [lang, setLang] = useState<Lang>(
    () => (localStorage.getItem(LANG_KEY) as Lang) ?? detectLang(),
  );
  const t = STRINGS[lang];
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

  useEffect(() => { localStorage.setItem(LANG_KEY, lang); }, [lang]);

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

  // 切換語言只重新取快速提問，不重設對話 ——
  // select() 會清空 turns，切語言時等於把使用者的對話紀錄刪掉。
  useEffect(() => {
    if (!activeId) return;
    api.getDocument(activeId, lang).then((d) => setQuick(d.quick_questions)).catch(() => {});
  }, [lang, activeId]);

  async function select(id: string) {
    setActiveId(id);
    setTurns([]);
    setReady(false);
    try {
      const doc = await api.getDocument(id, lang);
      setQuick(doc.quick_questions);
      setReady(true);
    } catch (e: any) {
      setError(describe(e));
    }
  }

  function describe(e: any): string {
    if (e instanceof api.HttpError) {
      if (e.detail) return e.detail;
      if (e.status === 413) return t.errorTooLarge;
      if (e.status === 502) return t.errorBackend;
      if (e.status === 504) return t.errorTimeout;
      return t.errorGeneric(e.status);
    }
    return e?.message ?? String(e);
  }

  const handleUpload = (file: File) => start(() => api.upload(file), file.name);
  const handleUploadUrl = (url: string) =>
    start(() => api.uploadFromUrl(url), lang === "zh" ? "由網址匯入" : "From link");

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
      setError(describe(e));
    }
  }

  async function ask(question: string) {
    if (!activeId) return;
    setBusy(true);
    setTurns((t) => [...t, {
      question, answer: "", sources: [], debug: null,
      stage: null, progress: null, declined: false, truncated: false,
    }]);

    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((all) => all.map((t, i) => (i === all.length - 1 ? fn(t) : t)));

    try {
      for await (const ev of api.askStream(activeId, question)) {
        if (ev.type === "stage")
          patch((t) => ({
            ...t,
            stage: ev.stage,
            // 摘要階段帶進度；一般問答的階段沒有 total，維持既有顯示
            progress: ev.stage.startsWith("summary_") && ev.total
              ? { phase: ev.stage.slice("summary_".length),
                  done: ev.done ?? 0, total: ev.total }
              : t.progress,
          }));
        else if (ev.type === "token") patch((t) => ({ ...t, answer: t.answer + ev.text }));
        else if (ev.type === "done")
          patch((t) => ({
            ...t, sources: ev.sources, debug: ev.debug,
            stage: null, progress: null, declined: ev.debug.declined === true,
            truncated: ev.debug.answer_truncated === true,
          }));
      }
    } catch (e: any) {
      patch((turn) => ({
        ...turn, answer: turn.answer || t.answerFailed(describe(e)), stage: null,
      }));
    } finally {
      setBusy(false);
    }
  }

  /** 標記取材自 PEGA AI 識別的長條＋弧形結構，重新繪製而非沿用原圖。 */
  const Mark = () => (
    <svg width="20" height="18" viewBox="0 0 20 18" aria-hidden="true" className="shrink-0">
      <rect x="0" y="8" width="2.6" height="9" rx="1" fill="#DDBE6E" />
      <rect x="4" y="4" width="2.6" height="13" rx="1" fill="#DDBE6E" />
      <path d="M8.4 17V1h1.8a8 8 0 0 1 0 16z" fill="#DDBE6E" />
    </svg>
  );

  const scrollToSource = (n: number) => {
    document.getElementById(`src-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const sources = turns.length ? turns[turns.length - 1].sources : [];
  const canAsk = ready && !!activeId;

  return (
    <div className="h-full flex flex-col bg-surface">
      <header className="flex items-center gap-2.5 px-4 py-2.5 bg-brand text-white">
        <Mark />
        <h1 className="text-base font-medium tracking-wide">
          Cite<span className="text-accent-gold">Lens</span>
        </h1>
        <span className="text-xs text-white/55">{t.subtitle}</span>
        <button
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          className="ml-auto rounded-lg border border-white/25 px-2 py-0.5 text-xs
                     text-white/80 hover:border-accent-gold hover:text-accent-gold
                     transition-colors"
          aria-label={lang === "zh" ? "Switch to English" : "切換為中文"}
        >
          {lang === "zh" ? "EN" : "中"}
        </button>
      </header>

      <div className="flex-1 min-h-0 flex">
        <DocumentPanel
          documents={documents} activeId={activeId} job={job} error={error} t={t}
          onSelect={select} onUpload={handleUpload} onUploadUrl={handleUploadUrl}
        />
        <Chat
          turns={turns} quickQuestions={quick} ready={canAsk} busy={busy} t={t}
          onAsk={ask} onCite={scrollToSource}
        />
        <SourcePanel sources={sources} t={t} />
      </div>
    </div>
  );
}
