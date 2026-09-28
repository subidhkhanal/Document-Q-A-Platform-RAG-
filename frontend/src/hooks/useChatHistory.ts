"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import type { Message, Conversation } from "@/types/chat";

const DEFAULT_STORAGE_KEY = "kb_conversations";
const MAX_CONVERSATIONS = 50;

// Generate title from first user message
function generateTitle(content: string): string {
  const maxLength = 30;
  const cleaned = content.trim().replace(/\n/g, ' ');
  if (cleaned.length <= maxLength) return cleaned;
  return cleaned.substring(0, maxLength).trim() + '...';
}

export function useChatHistory(storageKey: string = DEFAULT_STORAGE_KEY) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [isLoaded, setIsLoaded] = useState(false);
  // Source of truth for which conversation writes go to (state lags behind by a render).
  const currentIdRef = useRef<string | null>(null);

  // Load conversations from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length > 0) {
          setConversations(parsed);
          currentIdRef.current = parsed[0].id;
          setCurrentConversationId(parsed[0].id);
        }
      }
    } catch (e) {
      console.error("Failed to load conversations:", e);
    }
    setIsLoaded(true);
  }, []);

  // Save conversations to localStorage whenever they change
  useEffect(() => {
    if (!isLoaded) return;
    try {
      const toStore = conversations.slice(0, MAX_CONVERSATIONS);
      localStorage.setItem(storageKey, JSON.stringify(toStore));
    } catch (e) {
      console.error("Failed to save conversations:", e);
    }
  }, [conversations, isLoaded]);

  // Get current conversation
  const currentConversation = conversations.find(c => c.id === currentConversationId);
  const messages = currentConversation?.messages || [];


  // Create new conversation. The ref is updated synchronously so a setMessages call
  // in the same event handler (or from a streaming callback created before the
  // re-render) targets the new conversation instead of a stale id.
  const createConversation = useCallback(() => {
    const newConv: Conversation = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      title: 'New Chat',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: [],
    };
    currentIdRef.current = newConv.id;
    setConversations(prev => [newConv, ...prev.slice(0, MAX_CONVERSATIONS - 1)]);
    setCurrentConversationId(newConv.id);
    return newConv.id;
  }, []);

  // Set messages for current conversation
  const setMessages = useCallback((updater: Message[] | ((prev: Message[]) => Message[])) => {
    const targetId = currentIdRef.current ?? createConversation();

    setConversations(prev => {
      const target = prev.find(c => c.id === targetId);
      const newMessages = typeof updater === 'function' ? updater(target?.messages || []) : updater;

      return prev.map(conv => {
        if (conv.id === targetId) {
          // Update title if this is the first user message
          let title = conv.title;
          if (conv.messages.length === 0 && newMessages.length > 0) {
            const firstUserMessage = newMessages.find(m => m.role === 'user');
            if (firstUserMessage) {
              title = generateTitle(firstUserMessage.content);
            }
          }
          return {
            ...conv,
            title,
            updatedAt: Date.now(),
            messages: newMessages,
          };
        }
        return conv;
      });
    });
  }, [createConversation]);

  // Select a conversation
  const selectConversation = useCallback((id: string) => {
    currentIdRef.current = id;
    setCurrentConversationId(id);
  }, []);

  // Delete a conversation
  const deleteConversation = useCallback((id: string) => {
    const remaining = conversations.filter(c => c.id !== id);
    setConversations(prev => prev.filter(c => c.id !== id));
    // If we deleted the current conversation, switch to another
    if (id === currentIdRef.current) {
      const nextId = remaining.length > 0 ? remaining[0].id : null;
      currentIdRef.current = nextId;
      setCurrentConversationId(nextId);
    }
  }, [conversations]);

  // Clear current conversation messages
  const clearHistory = useCallback(() => {
    if (currentConversationId) {
      deleteConversation(currentConversationId);
    }
  }, [currentConversationId, deleteConversation]);

  // Rename a conversation
  const renameConversation = useCallback((id: string, newTitle: string) => {
    const trimmedTitle = newTitle.trim();
    if (!trimmedTitle) return;

    setConversations(prev => prev.map(conv => {
      if (conv.id === id) {
        return {
          ...conv,
          title: trimmedTitle,
          updatedAt: Date.now(),
        };
      }
      return conv;
    }));
  }, []);

  return {
    messages,
    setMessages,
    conversations,
    currentConversationId,
    createConversation,
    selectConversation,
    deleteConversation,
    renameConversation,
    clearHistory,
    isLoaded,
  };
}
