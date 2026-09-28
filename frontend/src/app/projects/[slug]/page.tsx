"use client";

import { use, useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { Header } from "@/components/Header";
import { useProject, useDeleteDocument } from "@/hooks/useProjects";
import { useToasts } from "@/hooks/useToasts";
import { useUpload } from "@/hooks/useUpload";
import { ToastContainer } from "@/components/Toast";
import { UploadModal } from "@/components/UploadModal";

const sourceConfig: Record<string, { label: string; color: string; bg: string }> = {
  epub: { label: "EPUB", color: "#8b5cf6", bg: "rgba(139, 92, 246, 0.1)" },
  pdf: { label: "PDF", color: "#dc2626", bg: "rgba(220, 38, 38, 0.1)" },
  docx: { label: "DOCX", color: "#2563eb", bg: "rgba(37, 99, 235, 0.1)" },
  txt: { label: "TXT", color: "#64748b", bg: "rgba(100, 116, 139, 0.1)" },
  md: { label: "MD", color: "#0d9488", bg: "rgba(13, 148, 136, 0.1)" },
  markdown: { label: "MD", color: "#0d9488", bg: "rgba(13, 148, 136, 0.1)" },
};

const statusConfig: Record<string, { label: string; color: string }> = {
  PROCESSING: { label: "Processing", color: "#d97706" },
  FAILED: { label: "Failed", color: "#dc2626" },
};

function getSourceInfo(source: string) {
  return sourceConfig[source] || { label: source, color: "#64748b", bg: "rgba(100, 116, 139, 0.1)" };
}

function TrashIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
    </svg>
  );
}

function ProjectDetailContent({ slug }: { slug: string }) {
  const { project, isLoading, error, refetch } = useProject(slug);
  const { toasts, addToast, removeToast, updateToast } = useToasts();
  const { uploadFile } = useUpload({ addToast, removeToast, updateToast });
  const [showUploadModal, setShowUploadModal] = useState(false);

  const { deleteDocument, isDeleting } = useDeleteDocument();

  const [deletingDocument, setDeletingDocument] = useState<{ id: number; source: string } | null>(null);

  // Close dialog on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDeletingDocument(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleDeleteDocument = useCallback(async () => {
    if (!deletingDocument) return;
    try {
      await deleteDocument(deletingDocument.id);
      addToast({ type: "success", message: "Document deleted" });
      setDeletingDocument(null);
      refetch();
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Failed to delete document" });
    }
  }, [deletingDocument, deleteDocument, addToast, refetch]);

  return (
    <div className="flex h-screen flex-col" style={{ background: "var(--bg-primary)" }}>
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      <AnimatePresence>
        {showUploadModal && (
          <UploadModal
            onClose={() => setShowUploadModal(false)}
            onUpload={(file, projectId) => uploadFile(file, { projectId, onAccepted: refetch, onSuccess: refetch })}
            defaultProjectId={project?.id ?? null}
          />
        )}
      </AnimatePresence>

      <Header onUploadClick={() => setShowUploadModal(true)} />

      <div className="flex-1 overflow-y-auto">
        <main className="mx-auto max-w-4xl px-6 py-12">
          {/* Back link */}
          <Link
            href="/projects"
            className="mb-6 inline-flex items-center gap-1.5 text-sm"
            style={{ color: "var(--text-secondary)" }}
          >
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 19l-7-7 7-7" />
            </svg>
            Back to Projects
          </Link>

          {/* Loading */}
          {isLoading && (
            <div className="flex justify-center py-16">
              <svg
                className="h-6 w-6 animate-spin"
                style={{ color: "var(--accent)" }}
                viewBox="0 0 24 24"
                fill="none"
              >
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            </div>
          )}

          {/* Error */}
          {error && (
            <div
              className="flex items-center gap-3 rounded-xl p-4"
              style={{ background: "var(--error-bg)" }}
            >
              <svg className="h-5 w-5" style={{ color: "var(--error)" }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p className="text-sm" style={{ color: "var(--error)" }}>{error}</p>
            </div>
          )}

          {/* Project content */}
          {project && (
            <>
              {/* Project header */}
              <div className="mb-8">
                <h1
                  className="text-2xl font-semibold tracking-tight"
                  style={{ color: "var(--text-primary)" }}
                >
                  {project.title}
                </h1>
                {project.description && (
                  <p
                    className="mt-2 text-sm leading-relaxed"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    {project.description}
                  </p>
                )}
                <p
                  className="mt-2 text-xs"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  {project.document_count} {project.document_count === 1 ? "document" : "documents"}
                </p>
              </div>

              {/* Documents Section */}
              <section>
                {project.documents.length === 0 && (
                  <div
                    className="flex flex-col items-center justify-center rounded-xl py-12 text-center"
                    style={{
                      background: "var(--bg-secondary)",
                      border: "1px solid var(--border)",
                      boxShadow: "var(--shadow-card)",
                    }}
                  >
                    <div
                      className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl"
                      style={{
                        background: "var(--accent-subtle)",
                        border: "1px solid var(--border-accent)",
                      }}
                    >
                      <svg className="h-6 w-6" style={{ color: "var(--accent)" }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                      </svg>
                    </div>
                    <p className="mb-1 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                      No documents yet
                    </p>
                    <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
                      Upload documents to this project
                    </p>
                  </div>
                )}

                {project.documents.length > 0 && (
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    {project.documents.map((doc, index) => {
                      const src = getSourceInfo(doc.source_type || "epub");
                      return (
                        <motion.div
                          key={`doc-${doc.document_id ?? doc.source}`}
                          initial={{ opacity: 0, y: 24 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{
                            duration: 0.6,
                            ease: [0.16, 1, 0.3, 1],
                            delay: index * 0.05,
                          }}
                          className="group relative flex flex-col gap-3 rounded-xl p-4"
                          style={{
                            background: "var(--bg-secondary)",
                            border: "1px solid var(--border)",
                            boxShadow: "var(--shadow-card)",
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--border-hover)"; }}
                          onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border)"; }}
                        >
                          {doc.document_id && doc.can_manage !== false && (
                            <button
                              className="absolute top-3 right-3 rounded-lg p-1.5 opacity-0 transition-opacity cursor-pointer group-hover:opacity-100"
                              style={{ color: "var(--text-tertiary)" }}
                              onMouseEnter={(e) => { e.currentTarget.style.color = "var(--error)"; e.currentTarget.style.background = "var(--error-bg)"; }}
                              onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-tertiary)"; e.currentTarget.style.background = "transparent"; }}
                              onClick={(e) => { e.stopPropagation(); setDeletingDocument({ id: doc.document_id!, source: doc.source }); }}
                            >
                              <TrashIcon />
                            </button>
                          )}

                          <div className="flex items-center gap-2">
                            <span
                              className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium"
                              style={{ background: src.bg, color: src.color }}
                            >
                              <span className="h-1.5 w-1.5 rounded-full" style={{ background: src.color }} />
                              {src.label}
                            </span>
                          </div>

                          <h3 className="text-sm font-medium leading-snug line-clamp-2" style={{ color: "var(--text-primary)" }}>
                            {doc.source}
                          </h3>

                          <div className="flex items-center gap-3 text-xs" style={{ color: "var(--text-tertiary)" }}>
                            <span>{doc.chunk_count} {doc.chunk_count === 1 ? "chunk" : "chunks"}</span>
                            {doc.active_version ? <span>v{doc.active_version}</span> : null}
                            {doc.page_count ? <span>{doc.page_count} pages</span> : null}
                            {doc.status && statusConfig[doc.status] && (
                              <span style={{ color: statusConfig[doc.status].color }}>
                                {statusConfig[doc.status].label}
                              </span>
                            )}
                          </div>
                          {doc.status === "FAILED" && doc.latest_error && (
                            <p className="text-xs line-clamp-2" style={{ color: "var(--error)" }} title={doc.latest_error}>
                              {doc.latest_error}
                            </p>
                          )}
                        </motion.div>
                      );
                    })}
                  </div>
                )}
              </section>
            </>
          )}

          {/* Delete confirmation dialog */}
          <AnimatePresence>
            {deletingDocument && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="fixed inset-0 z-50 flex items-center justify-center p-4"
                style={{ background: "rgba(0, 0, 0, 0.3)" }}
                onClick={() => setDeletingDocument(null)}
              >
                <motion.div
                  initial={{ opacity: 0, scale: 0.96, y: 12 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.96, y: 12 }}
                  transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                  className="w-full max-w-sm rounded-xl p-5"
                  style={{
                    background: "var(--bg-primary)",
                    border: "1px solid var(--border)",
                    boxShadow: "0 1px 3px rgba(0,0,0,0.06), 0 8px 24px rgba(0,0,0,0.1)",
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <h3
                    className="mb-2 text-sm font-semibold"
                    style={{ color: "var(--text-primary)" }}
                  >
                    Delete Document
                  </h3>
                  <p
                    className="mb-5 text-sm leading-relaxed"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    Are you sure you want to delete &ldquo;{deletingDocument.source}&rdquo;? This will permanently remove it and its vector embeddings.
                  </p>
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={() => setDeletingDocument(null)}
                      className="rounded-lg px-3 py-1.5 text-sm cursor-pointer"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleDeleteDocument}
                      disabled={isDeleting}
                      className="rounded-lg px-4 py-1.5 text-sm font-medium cursor-pointer disabled:opacity-50"
                      style={{
                        background: "var(--error)",
                        color: "white",
                      }}
                    >
                      {isDeleting ? "Deleting..." : "Delete Document"}
                    </button>
                  </div>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  return <ProjectDetailContent slug={slug} />;
}
