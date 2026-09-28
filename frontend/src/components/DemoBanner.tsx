"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/fetcher";

interface Me {
  username: string;
  is_guest: boolean;
  limits: { max_upload_mb: number; max_documents: number | null; data_expires_hours: number | null };
}

const DISMISS_KEY = "kb_demo_banner_dismissed";

function readDismissed(): boolean {
  try {
    return sessionStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

/** Explains the public demo to guests: shared read-only library, private expiring uploads. */
export function DemoBanner() {
  const { data: me } = useSWR<Me>("/api/v1/me", fetcher, { revalidateOnFocus: false });
  const [dismissed, setDismissed] = useState(readDismissed);

  if (!me?.is_guest || dismissed) return null;

  const dismiss = () => {
    setDismissed(true);
    try {
      sessionStorage.setItem(DISMISS_KEY, "1");
    } catch {
      // ignore
    }
  };

  return (
    <div
      className="flex items-start gap-3 px-4 py-2 text-xs md:text-sm"
      style={{ background: "var(--accent-subtle)", borderBottom: "1px solid var(--border-accent)", color: "var(--text-secondary)" }}
    >
      <p className="flex-1 leading-relaxed">
        <span className="font-semibold" style={{ color: "var(--text-primary)" }}>Live demo.</span>{" "}
        You have a private guest session. Ask questions about the shared <b>Sample Library</b> (a fictional
        company&apos;s policies; read-only), or upload up to {me.limits.max_documents ?? "a few"} files of up to{" "}
        {me.limits.max_upload_mb} MB. Your uploads are visible only to you and deleted after{" "}
        {me.limits.data_expires_hours ?? 24} hours. Answers cite their sources, and questions the documents
        can&apos;t answer get &ldquo;I don&apos;t know&rdquo;.
      </p>
      <button onClick={dismiss} className="shrink-0 cursor-pointer rounded px-2 py-0.5 hover:bg-bg-hover" aria-label="Dismiss">
        ✕
      </button>
    </div>
  );
}
