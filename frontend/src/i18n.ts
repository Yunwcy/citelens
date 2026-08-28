/** 介面文案。英文為手寫，非逐字翻譯 ——
 *  兩種語言各自遵循同一個原則：標籤式，不是教學式。 */

export type Lang = "zh" | "en";

/** 文案的形狀。明確宣告而非由字典推導 ——
 *  推導出來的是字面型別，兩種語言會被視為不相容。 */
export interface Strings {
  subtitle: string;
  documents: string;
  dropPdf: string;
  pdfLimit: string;
  orPasteLink: string;
  deleteDoc: string;
  confirmDelete: string;
  cancel: string;
  linkPlaceholder: string;
  import: string;
  pages: (n: number) => string;
  passages: (n: number) => string;
  tables: (n: number) => string;
  stillProcessing: string;
  stage: Record<string, string>;

  chat: string;
  emptyTitleNoDoc: string;
  emptyBodyNoDoc: string;
  emptyTitleReady: string;
  emptyBodyReady: string;
  askPlaceholder: string;
  send: string;
  answering: string;
  streamStage: Record<string, string>;
  summaryPhase: (step: number, label: string, done: number, total: number) => string;
  summaryStepLabel: Record<string, string>;
  stripMode: Record<string, string>;
  declinedBadge: string;
  truncatedBadge: string;
  streamErrorBadge: string;
  retryHint: string;

  sources: string;
  retrievedPassages: string;
  noCitationNote: string;
  sourcesEmpty: string;
  clickToExpand: string;
  page: (n: number) => string;
  tableTag: string;
  summaryTag: string;

  details: string;
  route: string;
  passagesLabel: string;
  contextLabel: string;
  usage: string;
  latency: string;
  retrievedUsed: (a: number, b: number) => string;
  droppedNote: (n: number) => string;
  tokensInOut: (a: string, b: string) => string;
  latencyValue: (a: number, b: number) => string;
  routeLabel: Record<string, string>;

  errorTooLarge: string;
  errorBackend: string;
  errorTimeout: string;
  errorGeneric: (n: number) => string;
  answerFailed: (m: string) => string;
}

export const STRINGS: Record<Lang, Strings> = {
  zh: {
    subtitle: "文件問答 · 答案附出處",
    documents: "文件",
    dropPdf: "拖曳 PDF 到這裡",
    pdfLimit: "PDF · 最大 30MB",
    orPasteLink: "或貼上連結",
    deleteDoc: "刪除這份文件",
    confirmDelete: "刪除？此動作無法復原",
    cancel: "取消",
    linkPlaceholder: "arXiv 網址或 PDF 連結",
    import: "匯入",
    pages: (n: number) => `${n} 頁`,
    passages: (n: number) => `${n} 個片段`,
    tables: (n: number) => `${n} 張表`,
    stillProcessing: "處理中，可繼續使用其他文件",
    stage: {
      queued: "排隊中", parsing: "解析中", indexing: "建立索引",
      summarizing: "摘要準備中", ready: "就緒", failed: "無法解析",
    },

    chat: "對話",
    emptyTitleNoDoc: "先上傳一份文件",
    emptyBodyNoDoc: "接著就能針對內容提問，回答會附上頁碼與章節出處",
    emptyTitleReady: "開始提問",
    emptyBodyReady: "回答會附上頁碼與章節出處",
    askPlaceholder: "針對這份文件提問",
    send: "送出",
    answering: "回答中…",
    streamStage: {
      retrieving: "檢索中…", packing: "整理片段…", generating: "產生回答…",
    },
    summaryStepLabel: { map: "逐節摘要", merge: "合併摘要" },
    summaryPhase: (step, label, done, total) =>
      `全文摘要 · 步驟 ${step}/2：${label}` + (total > 1 ? `（${done}/${total}）` : "…"),
    stripMode: {
      qa: "混合檢索", comparison: "多路檢索", table_lookup: "表格定位", summary: "階層式摘要",
    },
    declinedBadge: "文件未涵蓋此問題",
    truncatedBadge: "答案達長度上限，內容可能不完整",
    streamErrorBadge: "回答服務目前無法連線。",
    retryHint: "文件已完成索引，恢復連線後再問一次即可。",

    sources: "引用來源",
    retrievedPassages: "檢索到的片段",
    noCitationNote: "本次回答未標註引用編號，以下為送進模型的片段",
    sourcesEmpty: "提問後會顯示答案依據的段落",
    clickToExpand: "點擊可展開原文",
    page: (n: number) => `第 ${n} 頁`,
    tableTag: "表格",
    summaryTag: "章節摘要",

    details: "檢索細節",
    route: "路由",
    passagesLabel: "片段",
    contextLabel: "內容量",
    usage: "用量",
    latency: "耗時",
    retrievedUsed: (a: number, b: number) => `取回 ${a} 個，採用 ${b} 個`,
    droppedNote: (n: number) => ` · 因預算不足捨棄 ${n} 個`,
    tokensInOut: (a: string, b: string) => `${a} 進 / ${b} 出`,
    latencyValue: (a: number, b: number) => `檢索 ${a} ms · 生成 ${b} ms`,
    routeLabel: {
      summary: "摘要", comparison: "比較", table_lookup: "表格查詢", qa: "一般問答",
    },

    errorTooLarge: "檔案太大，上限為 30MB",
    errorBackend: "後端沒有回應，請確認服務是否啟動",
    errorTimeout: "處理逾時，請重試",
    errorGeneric: (n: number) => `伺服器錯誤（HTTP ${n}）`,
    answerFailed: (m: string) => `回答失敗：${m}`,
  },

  en: {
    subtitle: "Document Q&A · every answer cites its source",
    documents: "Documents",
    dropPdf: "Drop a PDF here",
    pdfLimit: "PDF · 30 MB max",
    orPasteLink: "Or paste a link",
    deleteDoc: "Delete this document",
    confirmDelete: "Delete? This cannot be undone",
    cancel: "Cancel",
    linkPlaceholder: "arXiv URL or PDF link",
    import: "Import",
    pages: (n: number) => `${n} page${n === 1 ? "" : "s"}`,
    passages: (n: number) => `${n} passage${n === 1 ? "" : "s"}`,
    tables: (n: number) => `${n} table${n === 1 ? "" : "s"}`,
    stillProcessing: "Still processing · other documents remain available",
    stage: {
      queued: "Queued", parsing: "Parsing", indexing: "Indexing",
      summarizing: "Summarizing", ready: "Ready", failed: "Can't parse",
    },

    chat: "Chat",
    emptyTitleNoDoc: "Upload a document first",
    emptyBodyNoDoc: "Then ask about its contents — answers cite the page and section they came from",
    emptyTitleReady: "Ask a question",
    emptyBodyReady: "Answers cite the page and section they came from",
    askPlaceholder: "Ask about this document",
    send: "Send",
    answering: "Answering…",
    streamStage: {
      retrieving: "Searching…", packing: "Assembling passages…",
      generating: "Writing the answer…",
    },
    summaryStepLabel: { map: "Section summaries", merge: "Merging" },
    summaryPhase: (step, label, done, total) =>
      `Document-wide summary · Step ${step}/2: ${label}` +
      (total > 1 ? ` (${done}/${total})` : "…"),
    stripMode: {
      qa: "Hybrid search", comparison: "Multi-query retrieval",
      table_lookup: "Table lookup", summary: "Hierarchical summarization",
    },
    declinedBadge: "Not covered by this document",
    truncatedBadge: "Answer hit the length limit and may be incomplete",
    streamErrorBadge: "The answering service is unreachable.",
    retryHint: "The document is fully indexed — just ask again once the connection is back.",

    sources: "Sources",
    retrievedPassages: "Retrieved passages",
    noCitationNote: "This answer carries no citation markers — below are the passages sent to the model",
    sourcesEmpty: "The passages behind each answer appear here",
    clickToExpand: "Click to expand",
    page: (n: number) => `Page ${n}`,
    tableTag: "table",
    summaryTag: "section summary",

    details: "Retrieval details",
    route: "Route",
    passagesLabel: "Passages",
    contextLabel: "Context",
    usage: "Usage",
    latency: "Latency",
    retrievedUsed: (a: number, b: number) => `${a} retrieved, ${b} used`,
    droppedNote: (n: number) => ` · ${n} dropped, over budget`,
    tokensInOut: (a: string, b: string) => `${a} in / ${b} out`,
    latencyValue: (a: number, b: number) => `search ${a} ms · generation ${b} ms`,
    routeLabel: {
      summary: "Summary", comparison: "Comparison",
      table_lookup: "Table lookup", qa: "General",
    },

    errorTooLarge: "File is larger than the 30 MB limit",
    errorBackend: "No response from the backend — check that it is running",
    errorTimeout: "Timed out — please try again",
    errorGeneric: (n: number) => `Server error (HTTP ${n})`,
    answerFailed: (m: string) => `Couldn't answer: ${m}`,
  },
};

export function detectLang(): Lang {
  return navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}
