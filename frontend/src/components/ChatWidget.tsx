"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { useApi } from "@/hooks/useApi";
import { useChatHistory } from "@/hooks/useChatHistory";
import { ChatArea } from "./ChatArea";
import { ChatInput } from "./ChatInput";
import { Sidebar } from "./Sidebar";
import type { Citation, Source } from "@/types/chat";

interface QueueItem {
  type: "token" | "done" | "error";
  content: string;
  answer?: string;
  citations?: Citation[];
  invalidCitations?: string[];
  abstained?: boolean;
}

interface StreamEvent {
  type: "status" | "citation" | "token" | "done" | "error";
  text?: string;
  message?: string;
  answer?: string;
  citations?: Citation[];
  invalid_citations?: string[];
  abstained?: boolean;
}

const HIDDEN_PATHS = ["/settings"];
const DEFAULT_WIDTH = 420;
const MIN_WIDTH = 320;
const WIDGET_STORAGE_KEY = "kb_widget_conversations";
const SAMPLE_LIBRARY_SLUG = "sample-library";
const SAMPLE_QUESTIONS = [
  "What is the deductible for the Silver PPO plan?",
  "How many PTO days carry over, and when do they expire?",
  "What does NW-4471 fix?",
  "Who is the CEO of Northwind Labs?",
];

function getSlugFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/projects\/([^/]+)/);
  return match ? match[1] : null;
}

function formatSlug(slug: string): string {
  return decodeURIComponent(slug)
    .replace(/-[0-9a-f]{8,}$/i, "") // strip trailing UUID/hash suffix
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function getPillPlaceholder(pathname: string): string {
  if (pathname === "/projects") return "Ask about all projects...";

  const projectMatch = pathname.match(/^\/projects\/([^/]+)/);
  if (projectMatch) return `Ask about ${formatSlug(projectMatch[1])}...`;

  return "Ask about your documents...";
}

export function ChatWidget() {
  const pathname = usePathname();
  const { apiFetch } = useApi();

  const {
    messages,
    setMessages,
    conversations,
    currentConversationId,
    createConversation,
    selectConversation,
    deleteConversation,
    renameConversation,
    isLoaded,
  } = useChatHistory(WIDGET_STORAGE_KEY);

  const [isOpen, setIsOpen] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH);
  const [isResizing, setIsResizing] = useState(false);

  const tokenQueueRef = useRef<QueueItem[]>([]);
  const drainIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const prevSlugRef = useRef<string | null>(null);

  const slug = getSlugFromPath(pathname);
  const isHidden = HIDDEN_PATHS.includes(pathname);

  // Reset when navigating to a different project
  useEffect(() => {
    if (slug !== prevSlugRef.current) {
      setIsChatLoading(false);
      if (drainIntervalRef.current) {
        clearInterval(drainIntervalRef.current);
        drainIntervalRef.current = null;
      }
      tokenQueueRef.current = [];
      prevSlugRef.current = slug;
    }
  }, [slug]);

  useEffect(() => {
    return () => {
      if (drainIntervalRef.current) {
        clearInterval(drainIntervalRef.current);
      }
    };
  }, []);

  // Resize handlers
  const handleResizeMove = useCallback(
    (e: MouseEvent) => {
      const newWidth = window.innerWidth - e.clientX;
      setPanelWidth(Math.max(MIN_WIDTH, newWidth));
    },
    []
  );

  const handleResizeEnd = useCallback(() => {
    setIsResizing(false);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  useEffect(() => {
    if (isResizing) {
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", handleResizeMove);
      window.addEventListener("mouseup", handleResizeEnd);
    }
    return () => {
      window.removeEventListener("mousemove", handleResizeMove);
      window.removeEventListener("mouseup", handleResizeEnd);
    };
  }, [isResizing, handleResizeMove, handleResizeEnd]);

  const startDraining = (assistantId: string) => {
    if (drainIntervalRef.current) return;

    drainIntervalRef.current = setInterval(() => {
      const queue = tokenQueueRef.current;
      if (queue.length === 0) return;

      const item = queue.shift()!;

      if (item.type === "token") {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: m.content + item.content }
              : m
          )
        );
      } else if (item.type === "done") {
        // Replace the streamed text with the server-validated answer (unverifiable
        // citation markers removed) and attach only the citations that were validated.
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: item.answer ?? m.content,
                  citations: item.citations,
                  invalidCitations: item.invalidCitations,
                  abstained: item.abstained,
                }
              : m
          )
        );
        clearInterval(drainIntervalRef.current!);
        drainIntervalRef.current = null;
      } else if (item.type === "error") {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: m.content ? `${m.content}\n\n${item.content}` : item.content }
              : m
          )
        );
        clearInterval(drainIntervalRef.current!);
        drainIntervalRef.current = null;
      }
    }, 30);
  };

  const handleNewChat = () => {
    createConversation();
    setShowHistory(false);
  };

  const handleSelectConversation = (id: string) => {
    selectConversation(id);
    setShowHistory(false);
  };

  const handleSubmit = async (inputText: string) => {
    if (isChatLoading) return;

    if (!isOpen) setIsOpen(true);
    if (showHistory) setShowHistory(false);

    // Auto-create a conversation if there's none active
    if (!currentConversationId) {
      createConversation();
    }

    const userMessage = {
      id: Date.now().toString(),
      role: "user" as const,
      content: inputText,
    };
    const assistantId = (Date.now() + 1).toString();

    setMessages((prev) => [
      ...prev,
      userMessage,
      { id: assistantId, role: "assistant" as const, content: "" },
    ]);
    setIsChatLoading(true);

    const handleEvent = (data: StreamEvent) => {
      if (data.type === "token") {
        const content = data.text ?? "";
        const pieces = content.length > 20 ? content.split(/(\s+)/) : [content];
        for (const piece of pieces) {
          if (piece) tokenQueueRef.current.push({ type: "token", content: piece });
        }
        startDraining(assistantId);
      } else if (data.type === "done") {
        tokenQueueRef.current.push({
          type: "done",
          content: "",
          answer: data.answer,
          citations: data.citations,
          invalidCitations: data.invalid_citations,
          abstained: data.abstained,
        });
        startDraining(assistantId);
      } else if (data.type === "error") {
        tokenQueueRef.current.push({ type: "error", content: data.message || "Something went wrong." });
        startDraining(assistantId);
      }
    };

    const parseChunk = (part: string) => {
      const line = part.trim();
      if (!line.startsWith("data: ")) return;
      try {
        handleEvent(JSON.parse(line.slice(6)) as StreamEvent);
      } catch {
        // Skip malformed JSON
      }
    };

    try {
      const chatHistory = messages
        .slice(-6)
        .filter((m) => m.content)
        .map((m) => ({ role: m.role, content: m.content.slice(0, 4000) }));

      const response = await apiFetch("/api/v1/qa/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: inputText,
          stream: true,
          project_slug: slug ? decodeURIComponent(slug) : undefined,
          chat_history: chatHistory.length > 0 ? chatHistory : undefined,
        }),
      });

      if (!response.ok) throw new Error("Query failed");

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response stream");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        parts.forEach(parseChunk);
      }

      // Process any remaining data left in buffer after stream ends
      if (buffer.trim()) parseChunk(buffer);
    } catch {
      if (drainIntervalRef.current) {
        clearInterval(drainIntervalRef.current);
        drainIntervalRef.current = null;
      }
      tokenQueueRef.current = [];

      setMessages((prev) => {
        const hasAssistant = prev.some((m) => m.id === assistantId);
        if (hasAssistant) {
          return prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content:
                    m.content ||
                    "Unable to connect. Please ensure the backend is running.",
                }
              : m
          );
        }
        return [
          ...prev,
          {
            id: assistantId,
            role: "assistant" as const,
            content:
              "Unable to connect. Please ensure the backend is running.",
          },
        ];
      });
    } finally {
      setIsChatLoading(false);
    }
  };

  const handleSourceClick = (_source: Source) => {
    // No-op in widget context
  };

  if (isHidden) return null;
  if (!isLoaded) return null;

  return (
    <>
      {/* Inline side panel — pushes content left */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: panelWidth, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={isResizing ? { duration: 0 } : { duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="relative flex flex-col shrink-0 overflow-hidden h-full"
            style={{
              background: "var(--bg-primary)",
              borderLeft: "1px solid var(--border)",
            }}
          >
            {/* Resize handle */}
            <div
              className="absolute left-0 top-0 bottom-0 z-10"
              style={{ width: "5px", cursor: "col-resize" }}
              onMouseDown={(e) => {
                e.preventDefault();
                setIsResizing(true);
              }}
            >
              <div
                className="absolute left-0 top-0 bottom-0 w-[3px] opacity-0 hover:opacity-100 transition-opacity"
                style={{ background: "var(--accent)" }}
              />
            </div>

            {/* Header */}
            <div
              className="flex items-center justify-between px-4 py-3 shrink-0"
              style={{ borderBottom: "1px solid var(--border)" }}
            >
              <div className="flex items-center gap-2">
                {/* History toggle */}
                <button
                  onClick={() => setShowHistory(!showHistory)}
                  className="flex h-8 w-8 items-center justify-center rounded-lg cursor-pointer hover:bg-bg-hover"
                  style={{ color: "var(--text-secondary)" }}
                  aria-label="Toggle sidebar"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                  </svg>
                </button>

                <h2
                  className="text-sm font-semibold"
                  style={{ color: "var(--text-primary)" }}
                >
                  {slug ? "Project Chat" : "Chat"}
                </h2>
              </div>

              <div className="flex items-center gap-1">
                {/* Minimize button */}
                <button
                  onClick={() => setIsOpen(false)}
                  className="flex h-7 w-7 items-center justify-center rounded-lg cursor-pointer"
                  style={{ color: "var(--text-tertiary)" }}
                  title="Minimize"
                  onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-secondary)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                >
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M20 12H4"
                    />
                  </svg>
                </button>
              </div>
            </div>

            {/* Body: sidebar + chat side by side */}
            <div className="flex flex-1 min-h-0">
              <Sidebar
                conversations={conversations}
                currentConversationId={currentConversationId}
                isOpen={showHistory}
                onToggle={() => setShowHistory(false)}
                onNewChat={handleNewChat}
                onSelectConversation={handleSelectConversation}
                onDeleteConversation={deleteConversation}
                onRenameConversation={renameConversation}
              />
              <div className="flex flex-1 flex-col min-w-0 min-h-0">
                <ChatArea
                  messages={messages}
                  onSourceClick={handleSourceClick}
                  compact
                  placeholder={getPillPlaceholder(pathname)}
                  suggestions={!slug || slug === SAMPLE_LIBRARY_SLUG ? SAMPLE_QUESTIONS : undefined}
                  onSuggestion={handleSubmit}
                />
                <ChatInput
                  onSubmit={handleSubmit}
                  isLoading={isChatLoading}
                  compact
                  placeholder={getPillPlaceholder(pathname)}
                />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Collapsed pill */}
      <AnimatePresence>
        {!isOpen && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-full cursor-pointer max-sm:right-4 max-sm:bottom-4"
            style={{
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              boxShadow:
                "0 4px 16px rgba(0, 0, 0, 0.08), 0 0 0 1px rgba(0, 0, 0, 0.04)",
              padding: "10px 16px",
            }}
            onClick={() => setIsOpen(true)}
          >
            {/* Chat icon */}
            <svg
              className="h-5 w-5 shrink-0"
              style={{ color: "var(--accent)" }}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
              />
            </svg>

            <span
              className="text-sm hidden sm:inline"
              style={{ color: "var(--text-tertiary)" }}
            >
              {getPillPlaceholder(pathname)}
            </span>

            {/* Arrow */}
            <div
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
              style={{ background: "var(--accent)" }}
            >
              <svg
                className="h-3.5 w-3.5 text-white"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2.5}
                  d="M5 12h14M12 5l7 7-7 7"
                />
              </svg>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
