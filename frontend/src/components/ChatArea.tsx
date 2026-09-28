"use client";

import { useRef, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { Message, Source } from "@/types/chat";
import { MessageBubble } from "./MessageBubble";
import { EmptyState } from "./EmptyState";

interface ChatAreaProps {
  messages: Message[];
  onSourceClick: (source: Source) => void;
  compact?: boolean;
  placeholder?: string;
  suggestions?: string[];
  onSuggestion?: (question: string) => void;
}

export function ChatArea({ messages, onSourceClick, compact, placeholder, suggestions, onSuggestion }: ChatAreaProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const prevCountRef = useRef(0);
  const [showScrollButton, setShowScrollButton] = useState(false);

  // Detect user scroll via wheel/touch
  useEffect(() => {
    const container = chatContainerRef.current;
    if (!container) return;

    const handleUserScroll = () => {
      requestAnimationFrame(() => {
        const { scrollTop, scrollHeight, clientHeight } = container;
        const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;
        shouldAutoScrollRef.current = isNearBottom;
        setShowScrollButton(!isNearBottom);
      });
    };

    container.addEventListener("wheel", handleUserScroll, { passive: true });
    container.addEventListener("touchmove", handleUserScroll, { passive: true });
    return () => {
      container.removeEventListener("wheel", handleUserScroll);
      container.removeEventListener("touchmove", handleUserScroll);
    };
  }, []);

  // Auto-scroll on new content only if user hasn't scrolled away
  useEffect(() => {
    if (shouldAutoScrollRef.current) {
      const container = chatContainerRef.current;
      if (container) container.scrollTop = container.scrollHeight;
      setShowScrollButton(false);
    }
  }, [messages]);

  // Track how many messages existed before this render so only new ones animate
  useEffect(() => {
    prevCountRef.current = messages.length;
  }, [messages.length]);

  const scrollToBottom = () => {
    const container = chatContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
      shouldAutoScrollRef.current = true;
      setShowScrollButton(false);
    }
  };

  return (
    <div className="relative flex-1 flex flex-col min-h-0">
      <main ref={chatContainerRef} className="flex-1 overflow-auto min-h-0">
        <div className={compact ? "px-4 py-4" : "mx-auto max-w-3xl px-6 py-8"}>
          {messages.length === 0 ? (
            compact ? (
              <div className="flex h-full flex-col items-center justify-center py-12 text-center">
                <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>
                  {placeholder || "Ask anything about your documents..."}
                </p>
                {suggestions && suggestions.length > 0 && onSuggestion && (
                  <div className="mt-5 flex w-full flex-col gap-2">
                    <p className="text-xs font-medium uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
                      Try the sample library
                    </p>
                    {suggestions.map((q) => (
                      <button
                        key={q}
                        onClick={() => onSuggestion(q)}
                        className="rounded-lg px-3 py-2 text-left text-sm cursor-pointer hover:bg-bg-hover"
                        style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <EmptyState />
            )
          ) : (
            <div className="space-y-6">
              {messages.map((message, index) => {
                const isNew = index >= prevCountRef.current;
                return (
                  <motion.div
                    key={message.id}
                    initial={isNew ? { opacity: 0, y: 24 } : false}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{
                      duration: 0.6,
                      ease: [0.16, 1, 0.3, 1] as const,
                    }}
                  >
                    <MessageBubble message={message} onSourceClick={onSourceClick} />
                  </motion.div>
                );
              })}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </main>

      {/* Scroll to bottom button */}
      <AnimatePresence>
        {showScrollButton && (
          <motion.button
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.2 }}
            onClick={scrollToBottom}
            className={`absolute flex items-center justify-center rounded-full cursor-pointer ${compact ? "right-3 bottom-2 h-8 w-8" : "right-6 bottom-4 h-10 w-10"}`}
            style={{
              background: "var(--accent)",
              color: "white",
              boxShadow: "0 2px 8px rgba(14, 165, 233, 0.3)",
            }}
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
            </svg>
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
}
