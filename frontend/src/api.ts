/** 後端介面。SSE 走 fetch 串流而非 EventSource —— 查詢是 POST，EventSource 只支援 GET。 */

const BASE = import.meta.env.VITE_API ?? "http://localhost:8000";

export type DocSummary = {
  doc_id: string;
  filename: string;
  uploaded: string | null;
  pages: number | null;
  chunks: number | null;
  tables: number | null;
  section_source: string | null;
  has_summary: boolean;
};

export type Source = {
  n: number; page: number; section: string; kind: string;
  chunk_id: string; score: number; text: string;
};

export type Debug = {
  route: string; route_reason: string; entities: string[]; table_id: string | null;
  retrieved: number; packed: number; context_tokens: number; context_budget: number;
  dropped: string[]; truncated: string[];
  prompt_tokens: number; completion_tokens: number; cost_usd: number;
  retrieval_ms: number; llm_ms: number; total_ms: number;
};

export type StreamEvent =
  | { type: "route"; route: string; reason: string; entities: string[]; table_id: string | null }
  | { type: "stage"; stage: string; packed?: number; tokens?: number; budget?: number }
  | { type: "token"; text: string }
  | { type: "done"; sources: Source[]; debug: Debug };

export type JobEvent = {
  job_id: string; doc_id: string; stage: string; error: string | null;
  pages?: number; sections?: number; chunks?: number; tables?: number; cached?: boolean;
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`);
  return res.json();
}

export const listDocuments = () => fetch(`${BASE}/api/documents`).then(json<DocSummary[]>);

export const getDocument = (id: string) =>
  fetch(`${BASE}/api/documents/${id}`).then(json<{ meta: any; tables: any[]; quick_questions: string[] }>);

export async function upload(file: File) {
  const form = new FormData();
  form.append("file", file);
  return json<{ job_id: string; doc_id: string }>(
    await fetch(`${BASE}/api/documents`, { method: "POST", body: form }),
  );
}

/** 逐行解析 SSE。共用於索引進度與查詢串流。 */
async function* sse(res: Response): AsyncGenerator<any> {
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (line.startsWith("data: ")) yield JSON.parse(line.slice(6));
    }
  }
}

export async function* jobEvents(jobId: string): AsyncGenerator<JobEvent> {
  yield* sse(await fetch(`${BASE}/api/jobs/${jobId}/events`));
}

export async function* askStream(docId: string, question: string): AsyncGenerator<StreamEvent> {
  yield* sse(await fetch(`${BASE}/api/query`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ doc_id: docId, question }),
  }));
}
