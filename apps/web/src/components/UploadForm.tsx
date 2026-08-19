"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { Dictionary } from "@/i18n";
import { ApiError, uploadSong } from "@/lib/api";

/**
 * Upload a local file (T-1.5's endpoint, from a screen).
 *
 * The file goes straight from the browser to the API rather than through
 * Next.js. Chapter 6 eventually wants a signed URL and no API in the path at
 * all; routing 30MB through a Node server first would be a step away from that,
 * not towards it.
 */
export function UploadForm({ t, locale }: { t: Dictionary; locale: string }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (file === null || busy) return;

    setBusy(true);
    setError(null);
    try {
      const result = await uploadSong(file);
      if (result.job_id === null) {
        // Deduplicated: the same audio was already here, so there is no job to
        // watch. Saying so beats sending the user to a progress screen for work
        // that will never happen.
        router.push(`/${locale}?existing=${result.id}`);
        return;
      }
      router.push(`/${locale}/jobs/${result.job_id}`);
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "unknown";
      setError(t.errors[code as keyof typeof t.errors] ?? t.errors.unknown);
      setBusy(false);
    }
  }

  return (
    <form className="card upload" onSubmit={submit}>
      <label className="file-field">
        <span className="file-label">{t.upload.choose}</span>
        <input
          type="file"
          accept="audio/*,video/mp4"
          disabled={busy}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setError(null);
          }}
        />
      </label>

      <p className="hint">{t.upload.hint}</p>

      {file ? (
        <p className="selected">
          {t.upload.selected}: <span className="filename">{file.name}</span>
        </p>
      ) : null}

      {error ? <p className="song-error">{error}</p> : null}

      <button type="submit" disabled={file === null || busy}>
        {busy ? t.upload.uploading : t.upload.submit}
      </button>
    </form>
  );
}
