import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 把文字節點中的 [1]、[2] 換成可點擊的引用標記。
 *  必須逐層處理子節點 —— 引用可能出現在段落、清單項或表格儲存格內。 */
function withCitations(children: ReactNode, onCite: (n: number) => void): ReactNode {
  if (typeof children === "string") {
    const parts = children.split(/(\[\d+\])/g);
    if (parts.length === 1) return children;
    return parts.map((part, i) => {
      const m = part.match(/^\[(\d+)\]$/);
      if (!m) return <span key={i}>{part}</span>;
      return (
        <button
          key={i}
          onClick={() => onCite(Number(m[1]))}
          className="text-accent hover:underline align-baseline"
        >
          {part}
        </button>
      );
    });
  }
  if (Array.isArray(children)) {
    return children.map((c, i) => <span key={i}>{withCitations(c, onCite)}</span>);
  }
  return children;
}

export function Markdown({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const cite = (children: ReactNode) => withCitations(children, onCite);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{cite(children)}</p>,
        strong: ({ children }) => <strong className="font-medium">{cite(children)}</strong>,
        em: ({ children }) => <em>{cite(children)}</em>,
        ul: ({ children }) => <ul className="mb-2 ml-4 list-disc space-y-1">{children}</ul>,
        ol: ({ children }) => <ol className="mb-2 ml-4 list-decimal space-y-1">{children}</ol>,
        li: ({ children }) => <li className="leading-relaxed">{cite(children)}</li>,
        h1: ({ children }) => <h3 className="font-medium mt-3 mb-1.5">{cite(children)}</h3>,
        h2: ({ children }) => <h3 className="font-medium mt-3 mb-1.5">{cite(children)}</h3>,
        h3: ({ children }) => <h3 className="font-medium mt-3 mb-1.5">{cite(children)}</h3>,
        code: ({ children }) => (
          <code className="rounded bg-surface-sunk px-1 py-0.5 font-mono text-xs">{children}</code>
        ),
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-line-strong pl-3 text-ink-soft">
            {children}
          </blockquote>
        ),
        // 表格是這個系統的核心產出，必須以表格呈現而非原始文字
        table: ({ children }) => (
          <div className="my-2 overflow-x-auto rounded-lg border border-line">
            <table className="w-full border-collapse text-xs">{children}</table>
          </div>
        ),
        thead: ({ children }) => <thead className="bg-surface-sunk">{children}</thead>,
        tr: ({ children }) => <tr className="border-b border-line last:border-0">{children}</tr>,
        th: ({ children }) => (
          <th className="whitespace-nowrap px-2.5 py-1.5 text-left font-medium">{cite(children)}</th>
        ),
        td: ({ children }) => (
          <td className="whitespace-nowrap px-2.5 py-1.5 tabular-nums">{cite(children)}</td>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
