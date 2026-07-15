"use client";

import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

interface MarkdownRendererProps {
  content: string;
}

const components: Components = {
  img: ({ src, alt, ...props }) => (
    <a href={src} target="_blank" rel="noopener noreferrer" className="block my-4">
      <img
        src={src}
        alt={alt || ""}
        className="w-full max-w-2xl rounded-lg border shadow-sm"
        loading="lazy"
        {...props}
      />
      {alt && (
        <span className="block text-center text-xs text-muted-foreground mt-1.5 italic">
          {alt}
        </span>
      )}
    </a>
  ),
  h1: ({ children, ...props }) => (
    <h1 className="text-2xl font-bold mt-8 mb-3 pb-1.5 border-b" {...props}>{children}</h1>
  ),
  h2: ({ children, ...props }) => (
    <h2 className="text-xl font-semibold mt-6 mb-2.5 pb-1 border-b border-border/50" {...props}>{children}</h2>
  ),
  h3: ({ children, ...props }) => (
    <h3 className="text-lg font-semibold mt-5 mb-2" {...props}>{children}</h3>
  ),
  p: ({ children, ...props }) => (
    <p className="my-2.5 leading-relaxed" {...props}>{children}</p>
  ),
  ul: ({ children, ...props }) => (
    <ul className="my-2.5 pl-6 list-disc space-y-1" {...props}>{children}</ul>
  ),
  ol: ({ children, ...props }) => (
    <ol className="my-2.5 pl-6 list-decimal space-y-1" {...props}>{children}</ol>
  ),
  li: ({ children, ...props }) => (
    <li className="leading-relaxed" {...props}>{children}</li>
  ),
  blockquote: ({ children, ...props }) => (
    <blockquote className="border-l-4 border-primary/30 pl-4 my-3 italic text-muted-foreground" {...props}>{children}</blockquote>
  ),
  code: ({ className, children, ...props }: any) => {
    const isInline = !className;
    if (isInline) {
      return (
        <code className="px-1.5 py-0.5 rounded bg-muted text-sm font-mono" {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className={`block p-3 rounded-lg bg-muted text-sm font-mono overflow-x-auto ${className || ""}`} {...props}>
        {children}
      </code>
    );
  },
  pre: ({ children, ...props }) => (
    <pre className="my-3" {...props}>{children}</pre>
  ),
  table: ({ children, ...props }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-sm" {...props}>{children}</table>
    </div>
  ),
  th: ({ children, ...props }) => (
    <th className="border px-3 py-2 bg-muted font-semibold text-left" {...props}>{children}</th>
  ),
  td: ({ children, ...props }) => (
    <td className="border px-3 py-2" {...props}>{children}</td>
  ),
  a: ({ children, href, ...props }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline decoration-primary/30 hover:decoration-primary" {...props}>
      {children}
    </a>
  ),
  hr: (props) => (
    <hr className="my-6 border-border" {...props} />
  ),
  strong: ({ children, ...props }) => (
    <strong className="font-semibold text-foreground" {...props}>{children}</strong>
  ),
};

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <div className="wiki-content text-foreground/90">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
