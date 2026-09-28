"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Citation, Message, Source } from "@/types/chat";

interface MessageBubbleProps {
  message: Message;
  onSourceClick: (source: Source) => void;
}

function CitationsList({ citations }: { citations: Citation[] }) {
  const [isOpen, setIsOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="mb-3">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 text-xs font-medium cursor-pointer rounded px-2 py-1 hover:bg-bg-hover"
        style={{ color: "var(--accent)" }}
      >
        <svg
          className={`h-3 w-3 transition-transform duration-200 ${isOpen ? "rotate-90" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        Citations ({citations.length})
      </button>
      {isOpen && (
        <ul className="mt-2 space-y-1.5 pl-5">
          {citations.map((c) => (
            <li key={c.chunk_id} className="text-xs" style={{ color: "var(--text-secondary)" }}>
              <button
                onClick={() => setExpanded(expanded === c.chunk_id ? null : c.chunk_id)}
                className="flex flex-wrap items-center gap-1.5 text-left cursor-pointer"
                title={`Chunk ${c.chunk_id}`}
              >
                <span className="font-semibold" style={{ color: "var(--accent)" }}>[{c.label}]</span>
                <span className="font-medium">{c.source_name}</span>
                <span style={{ color: "var(--text-tertiary)" }}>
                  v{c.document_version}
                  {c.page_number != null && ` · p.${c.page_number}`}
                  {c.section_title && ` · ${c.section_title}`}
                </span>
              </button>
              {expanded === c.chunk_id && c.text && (
                <p
                  className="mt-1 rounded-lg px-3 py-2 leading-relaxed whitespace-pre-wrap"
                  style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)" }}
                >
                  {c.text}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SourcesCollapsible({
  sources,
  onSourceClick,
}: {
  sources: Source[];
  onSourceClick: (s: Source) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const uniqueSources = Object.values(
    sources.reduce((acc, source) => {
      const key = source.source;
      if (!acc[key] || (source.similarity || 0) > (acc[key].similarity || 0)) {
        acc[key] = source;
      }
      return acc;
    }, {} as Record<string, Source>)
  );

  return (
    <div className="mb-3">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 text-xs font-medium cursor-pointer rounded px-2 py-1 hover:bg-bg-hover"
        style={{ color: "var(--accent)" }}
      >
        <svg
          className={`h-3 w-3 transition-transform duration-200 ${isOpen ? "rotate-90" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        Sources ({uniqueSources.length})
      </button>
      {isOpen && (
        <div className="mt-2 flex flex-wrap gap-2 pl-5">
          {uniqueSources.map((source, idx) => (
            <button
              key={idx}
              onClick={() => onSourceClick(source)}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs cursor-pointer hover:bg-bg-hover"
              style={{
                background: "var(--bg-tertiary)",
                color: "var(--text-secondary)",
                border: "1px solid var(--border)",
              }}
            >
              <svg
                className="h-3.5 w-3.5"
                style={{ color: "var(--accent)" }}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <span className="font-medium">{source.source}</span>
              {source.page && (
                <span style={{ color: "var(--text-tertiary)" }}>p.{source.page}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function MessageBubble({ message, onSourceClick }: MessageBubbleProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may not be available
    }
  };

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div
          className="max-w-[75%] rounded-2xl rounded-br-sm px-4 py-2.5"
          style={{ background: "var(--accent)" }}
        >
          <p className="text-sm leading-relaxed text-white">
            {message.content}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%]">
        <div
          className="rounded-2xl rounded-bl-sm px-4 py-3"
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
          }}
        >
          {/* Validated citations (v1) or sources from older saved conversations */}
          {message.citations && message.citations.length > 0 ? (
            <CitationsList citations={message.citations} />
          ) : (
            message.sources &&
            message.sources.length > 0 && (
              <SourcesCollapsible sources={message.sources} onSourceClick={onSourceClick} />
            )
          )}

          {/* Main content */}
          {message.content ? (
            <div className="prose-chat">
              <ReactMarkdown>{message.content}</ReactMarkdown>
              {message.invalidCitations && message.invalidCitations.length > 0 && (
                <p className="mt-2 text-xs" style={{ color: "var(--text-tertiary)" }}>
                  {message.invalidCitations.length} citation(s) could not be verified against your documents and were removed.
                </p>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-1.5 py-1">
              <span className="h-1.5 w-1.5 rounded-full animate-pulse-subtle" style={{ background: "var(--accent)" }} />
              <span className="h-1.5 w-1.5 rounded-full animate-pulse-subtle" style={{ background: "var(--accent)", animationDelay: "0.2s" }} />
              <span className="h-1.5 w-1.5 rounded-full animate-pulse-subtle" style={{ background: "var(--accent)", animationDelay: "0.4s" }} />
            </div>
          )}
        </div>

        {/* Copy button below the bubble */}
        {message.content && (
          <div className="mt-1 flex items-center gap-3 pl-2">
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 text-xs cursor-pointer rounded px-2 py-1 hover:bg-bg-hover"
              style={{ color: "var(--text-tertiary)" }}
            >
              {copied ? (
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              )}
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
