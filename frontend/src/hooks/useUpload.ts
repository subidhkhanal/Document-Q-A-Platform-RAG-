"use client";

import { useRef, useCallback, useEffect } from "react";
import { useApi } from "./useApi";
import type { Toast } from "@/types/chat";

interface ToastHandlers {
  addToast: (toast: Omit<Toast, "id">) => string;
  removeToast: (id: string) => void;
  updateToast: (id: string, updates: Partial<Toast>) => void;
}

interface UploadOptions {
  projectId?: number | null;
  /** Called once the server has accepted the upload (document is PROCESSING). */
  onAccepted?: () => void;
  /** Called when ingestion finishes (READY or FAILED). */
  onSuccess?: () => void;
}

interface IngestionJob {
  job_id: string;
  status: "PROCESSING" | "READY" | "FAILED";
  stage: string;
  indexed_chunks: number;
  error?: string | null;
}

const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 15 * 60 * 1000;

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  parsing: "Parsing document",
  chunking: "Chunking",
  embedding: "Generating embeddings",
  indexing: "Indexing",
  activating: "Activating version",
  retrying: "Retrying after a temporary error",
};

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

export function useUpload(toastHandlers: ToastHandlers) {
  const { createXhr, apiFetch } = useApi();
  const handlersRef = useRef(toastHandlers);
  useEffect(() => {
    handlersRef.current = toastHandlers;
  });
  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach(clearTimeout);
      timers.clear();
    };
  }, []);

  const pollJob = useCallback(
    (jobId: string, toastId: string, fileName: string, options: UploadOptions) => {
      const { addToast, removeToast, updateToast } = handlersRef.current;
      const startedAt = Date.now();

      const tick = async () => {
        let job: IngestionJob | null = null;
        try {
          const res = await apiFetch(`/api/v1/ingestion/jobs/${encodeURIComponent(jobId)}`);
          if (res.ok) job = await res.json();
        } catch {
          // transient network error: keep polling
        }

        if (job?.status === "READY") {
          removeToast(toastId);
          addToast({
            type: "success",
            message: "Document ready",
            subMessage: `${fileName} indexed (${job.indexed_chunks} chunks)`,
          });
          options.onSuccess?.();
          return;
        }
        if (job?.status === "FAILED") {
          removeToast(toastId);
          addToast({ type: "error", message: "Ingestion failed", subMessage: job.error || "Unknown error" });
          options.onSuccess?.();
          return;
        }
        if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          removeToast(toastId);
          addToast({
            type: "error",
            message: "Still processing",
            subMessage: `${fileName} is taking longer than expected; check back later`,
          });
          return;
        }
        if (job) {
          updateToast(toastId, { subMessage: STAGE_LABELS[job.stage] || "Processing..." });
        }
        const timer = setTimeout(() => {
          timersRef.current.delete(timer);
          void tick();
        }, POLL_INTERVAL_MS);
        timersRef.current.add(timer);
      };

      void tick();
    },
    [apiFetch]
  );

  const uploadFile = useCallback(
    async (file: File, options: UploadOptions = {}) => {
      const { addToast, removeToast, updateToast } = handlersRef.current;

      const toastId = addToast({
        type: "loading",
        message: `Uploading ${file.name}`,
        subMessage: "0%",
      });

      const formData = new FormData();
      formData.append("file", file);
      if (options.projectId) {
        formData.append("project_id", String(options.projectId));
      }

      const xhr = await createXhr("POST", "/api/v1/documents");
      // Same key on any retry of this upload, so the server never creates duplicate work.
      xhr.setRequestHeader("Idempotency-Key", newIdempotencyKey());

      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          const percent = Math.round((e.loaded / e.total) * 100);
          updateToast(toastId, {
            message: percent === 100 ? `Processing ${file.name}` : `Uploading ${file.name}`,
            subMessage: percent === 100 ? "Queued" : `${percent}%`,
          });
        }
      });

      xhr.addEventListener("load", () => {
        try {
          const data = JSON.parse(xhr.responseText);
          if (xhr.status >= 200 && xhr.status < 300 && data.job_id) {
            updateToast(toastId, { message: `Processing ${file.name}`, subMessage: "Queued" });
            options.onAccepted?.();
            pollJob(data.job_id, toastId, file.name, options);
          } else {
            removeToast(toastId);
            addToast({
              type: "error",
              message: "Upload failed",
              subMessage: typeof data.detail === "string" ? data.detail : "Unknown error",
            });
          }
        } catch {
          removeToast(toastId);
          addToast({ type: "error", message: "Upload failed", subMessage: "Invalid response" });
        }
      });

      xhr.addEventListener("error", () => {
        removeToast(toastId);
        addToast({ type: "error", message: "Upload failed", subMessage: "Could not connect to server" });
      });

      xhr.send(formData);
    },
    [createXhr, pollJob]
  );

  return { uploadFile };
}
