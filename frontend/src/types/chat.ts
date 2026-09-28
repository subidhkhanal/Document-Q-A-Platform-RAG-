export interface Source {
  source: string;
  page: number | null;
  similarity: number;
  chunk_id?: string;
  text?: string;
}

/** Provenance for one piece of evidence cited in an answer. */
export interface Citation {
  label: string;
  document_id: number;
  document_version: number;
  chunk_id: string;
  page_number: number | null;
  source_name: string;
  section_title?: string | null;
  text?: string;
}

export interface ChunkContext {
  id: string;
  text: string;
  source: string;
  source_type: string;
  page: number | null;
  chunk_index: number;
  total_chunks: number;
  prev_chunks: Array<{ text: string; chunk_index: number; page?: number }>;
  next_chunks: Array<{ text: string; chunk_index: number; page?: number }>;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  citations?: Citation[];
  /** Citation markers the model produced that did not match authorized evidence (removed from content). */
  invalidCitations?: string[];
  abstained?: boolean;
  provider?: string;
  timestamp?: number;
}

export interface Toast {
  id: string;
  type: "success" | "error" | "loading";
  message: string;
  subMessage?: string;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
}
