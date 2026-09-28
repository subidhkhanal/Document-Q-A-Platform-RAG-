"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { Header } from "@/components/Header";
import { useProjects, useCreateProject, useDeleteProject } from "@/hooks/useProjects";
import { useToasts } from "@/hooks/useToasts";
import { useUpload } from "@/hooks/useUpload";
import { ToastContainer } from "@/components/Toast";
import { UploadModal } from "@/components/UploadModal";

function formatRelativeDate(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
  return `${Math.floor(diffDays / 365)} years ago`;
}

export function ProjectsView() {
  const { projects, isLoading, error, refetch } = useProjects();
  const { createProject, isCreating } = useCreateProject();
  const { deleteProject, isDeleting } = useDeleteProject();
  const { toasts, addToast, removeToast, updateToast } = useToasts();
  const { uploadFile } = useUpload({ addToast, removeToast, updateToast });

  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [deletingSlug, setDeletingSlug] = useState<string | null>(null);
  const [showUploadModal, setShowUploadModal] = useState(false);

  // Close confirmation dialog on Escape
  useEffect(() => {
    if (!deletingSlug) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDeletingSlug(null);
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [deletingSlug]);

  const handleCreateProject = async () => {
    if (!newTitle.trim()) return;

    try {
      await createProject(newTitle.trim(), newDescription.trim());
      setNewTitle("");
      setNewDescription("");
      setShowCreateForm(false);
      refetch();
    } catch (err: any) {
      alert(err.message || "Failed to create project");
    }
  };

  const handleDeleteProject = async () => {
    if (!deletingSlug) return;
    try {
      await deleteProject(deletingSlug);
      setDeletingSlug(null);
      refetch();
    } catch (err: any) {
      alert(err.message || "Failed to delete project");
    }
  };

  return (
    <div className="h-screen overflow-y-auto" style={{ background: "var(--bg-primary)" }}>
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      <AnimatePresence>
        {showUploadModal && (
          <UploadModal
            onClose={() => setShowUploadModal(false)}
            onUpload={(file, projectId) => uploadFile(file, { projectId })}
          />
        )}
      </AnimatePresence>

      <Header onUploadClick={() => setShowUploadModal(true)} />

      <main className="mx-auto max-w-4xl px-6 py-12">
        {/* Page header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1
              className="text-2xl font-semibold tracking-tight"
              style={{ color: "var(--text-primary)" }}
            >
              Projects
            </h1>
            {!isLoading && (
              <p
                className="mt-1 text-sm"
                style={{ color: "var(--text-tertiary)" }}
              >
                {projects.length} {projects.length === 1 ? "project" : "projects"}
              </p>
            )}
          </div>
          <button
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium cursor-pointer"
            style={{
              background: "var(--accent)",
              color: "white",
            }}
          >
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New Project
          </button>
        </div>

        {/* Create project form */}
        {showCreateForm && (
          <motion.div
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-8 rounded-xl p-5"
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              boxShadow: "var(--shadow-card)",
            }}
          >
            <h3
              className="mb-4 text-sm font-semibold"
              style={{ color: "var(--text-primary)" }}
            >
              Create New Project
            </h3>
            <div className="space-y-3">
              <input
                type="text"
                placeholder="Project title"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreateProject()}
                className="w-full rounded-lg px-3 py-2 text-sm outline-none"
                style={{
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                }}
                autoFocus
              />
              <textarea
                placeholder="Description (optional)"
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
                rows={2}
                className="w-full resize-none rounded-lg px-3 py-2 text-sm outline-none"
                style={{
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                }}
              />
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => {
                    setShowCreateForm(false);
                    setNewTitle("");
                    setNewDescription("");
                  }}
                  className="rounded-lg px-3 py-1.5 text-sm cursor-pointer"
                  style={{ color: "var(--text-secondary)" }}
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreateProject}
                  disabled={!newTitle.trim() || isCreating}
                  className="rounded-lg px-4 py-1.5 text-sm font-medium cursor-pointer disabled:opacity-50"
                  style={{
                    background: "var(--accent)",
                    color: "white",
                  }}
                >
                  {isCreating ? "Creating..." : "Create"}
                </button>
              </div>
            </div>
          </motion.div>
        )}

        {/* Loading */}
        {isLoading && (
          <div className="flex justify-center py-16">
            <svg
              className="h-6 w-6 animate-spin"
              style={{ color: "var(--accent)" }}
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="3"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
          </div>
        )}

        {/* Error */}
        {error && (
          <div
            className="flex items-center gap-3 rounded-xl p-4"
            style={{ background: "var(--error-bg)" }}
          >
            <svg
              className="h-5 w-5"
              style={{ color: "var(--error)" }}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            <p className="text-sm" style={{ color: "var(--error)" }}>
              {error}
            </p>
          </div>
        )}

        {/* Empty state */}
        {!isLoading && !error && projects.length === 0 && (
          <div
            className="flex flex-col items-center justify-center rounded-xl py-16 text-center"
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
              <svg
                className="h-6 w-6"
                style={{ color: "var(--accent)" }}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"
                />
              </svg>
            </div>
            <p
              className="mb-1 text-sm font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              No projects yet
            </p>
            <p
              className="mb-4 text-xs"
              style={{ color: "var(--text-tertiary)" }}
            >
              Create a project to organize your documents
            </p>
            <button
              onClick={() => setShowCreateForm(true)}
              className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium cursor-pointer"
              style={{
                background: "var(--accent)",
                color: "white",
              }}
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Create your first project
            </button>
          </div>
        )}

        {/* Projects grid */}
        {!isLoading && !error && projects.length > 0 && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {projects.map((project, index) => (
              <Link key={project.slug} href={`/projects/${project.slug}`}>
                <motion.div
                  initial={{ opacity: 0, y: 24 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    duration: 0.6,
                    ease: [0.16, 1, 0.3, 1],
                    delay: index * 0.05,
                  }}
                  className="group relative flex flex-col gap-3 rounded-xl p-5 cursor-pointer"
                  style={{
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border)",
                    boxShadow: "var(--shadow-card)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = "var(--border-hover)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = "var(--border)";
                  }}
                >
                  {/* Delete button (not for projects shared read-only with you) */}
                  {!project.read_only && (
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setDeletingSlug(project.slug);
                    }}
                    className="absolute top-3 right-3 flex h-7 w-7 items-center justify-center rounded-lg cursor-pointer opacity-0 group-hover:opacity-100 transition-opacity"
                    style={{ color: "var(--text-tertiary)" }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = "var(--bg-tertiary)";
                      e.currentTarget.style.color = "var(--error)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "transparent";
                      e.currentTarget.style.color = "var(--text-tertiary)";
                    }}
                    title="Delete project"
                  >
                    <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                  )}

                  {/* Folder icon + title */}
                  <div className="flex items-start gap-3">
                    <div
                      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
                      style={{
                        background: "var(--accent-subtle)",
                      }}
                    >
                      <svg
                        className="h-4.5 w-4.5"
                        style={{ color: "var(--accent)" }}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={1.5}
                          d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"
                        />
                      </svg>
                    </div>
                    <div className="min-w-0 flex-1">
                      <h3
                        className="text-sm font-semibold leading-snug line-clamp-1"
                        style={{ color: "var(--text-primary)" }}
                      >
                        {project.title}
                      </h3>
                      {project.description && (
                        <p
                          className="mt-0.5 text-xs leading-relaxed line-clamp-2"
                          style={{ color: "var(--text-tertiary)" }}
                        >
                          {project.description}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Stats row */}
                  <div
                    className="flex items-center gap-3 text-xs"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    <span>{project.document_count} {project.document_count === 1 ? "document" : "documents"}</span>
                    <span className="h-0.5 w-0.5 rounded-full" style={{ background: "var(--text-tertiary)" }} />
                    <span>{formatRelativeDate(project.updated_at)}</span>
                    {project.read_only && (
                      <>
                        <span className="h-0.5 w-0.5 rounded-full" style={{ background: "var(--text-tertiary)" }} />
                        <span style={{ color: "var(--accent)" }}>Shared · read-only</span>
                      </>
                    )}
                  </div>
                </motion.div>
              </Link>
            ))}
          </div>
        )}

        {/* Delete confirmation dialog */}
        <AnimatePresence>
          {deletingSlug && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 z-50 flex items-center justify-center p-4"
              style={{ background: "rgba(0, 0, 0, 0.3)" }}
              onClick={() => setDeletingSlug(null)}
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
                  Delete Project
                </h3>
                <p
                  className="mb-5 text-sm leading-relaxed"
                  style={{ color: "var(--text-secondary)" }}
                >
                  Are you sure you want to delete &ldquo;{projects.find((p) => p.slug === deletingSlug)?.title}&rdquo;? All documents in this project will be removed.
                </p>
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => setDeletingSlug(null)}
                    className="rounded-lg px-3 py-1.5 text-sm cursor-pointer"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleDeleteProject}
                    disabled={isDeleting}
                    className="rounded-lg px-4 py-1.5 text-sm font-medium cursor-pointer disabled:opacity-50"
                    style={{
                      background: "var(--error)",
                      color: "white",
                    }}
                  >
                    {isDeleting ? "Deleting..." : "Delete Project"}
                  </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}
