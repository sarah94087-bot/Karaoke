"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { Dictionary } from "@/i18n";
import Link from "next/link";

import { ApiError, createSong, createUploadTicket, putToStorage } from "@/lib/api";

/**
 * Adding a song, in chapter 6's three steps (T-3.2).
 *
 * Ask the API where to put the file, PUT it **straight to storage**, then tell
 * the API the key. The bytes never pass through this app and, with the object
 * store, never through the API either - which is what makes a 30MB upload the
 * bucket's problem rather than a free instance's.
 *
 * The wait is real, so the screen shows a real percentage. Anything else on
 * this screen would be a spinner for the longest thing the user does here.
 */
export function UploadForm({ t, locale }: { t: Dictionary; locale: string }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [stage, setStage] = useState<"idle" | "preparing" | "sending" | "finishing">("idle");
  const [sent, setSent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // D-30: an over-quota message has to offer the way out, not just name the
  // problem. These are the codes where there is somewhere to go.
  const [showAccount, setShowAccount] = useState(false);
  const busy = stage !== "idle";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (file === null || busy) return;

    setError(null);
    setSent(0);
    try {
      setStage("preparing");
      const ticket = await createUploadTicket(file);

      setStage("sending");
      await putToStorage(ticket, file, setSent);

      setStage("finishing");
      const result = await createSong(ticket.key, file.name);
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
      setShowAccount(["storage_full", "monthly_songs_exhausted"].includes(code));
      setStage("idle");
    }
  }

  const percent = Math.round(sent * 100);

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

      {stage === "sending" ? (
        <div className="upload-progress">
          {/* The same bar the library rows and the job screen use, so a wait
              looks like a wait everywhere in the app. */}
          <div className="progress">
            <div className="progress-fill" style={{ inlineSize: `${percent}%` }} />
          </div>
          <p className="progress-line">
            <span>{t.upload.sending}</span>{" "}
            <span className="ltr-number">{t.upload.percent.replace("{n}", String(percent))}</span>
          </p>
        </div>
      ) : null}

      {stage === "preparing" || stage === "finishing" ? (
        <p className="progress-line">{t.upload[stage]}</p>
      ) : null}

      {error ? <p className="song-error">{error}</p> : null}
      {showAccount ? (
        <Link className="auth-link" href={`/${locale}/account`}>
          {t.account.freeUpTitle}
        </Link>
      ) : null}

      <button type="submit" disabled={file === null || busy}>
        {busy ? t.upload.uploading : t.upload.submit}
      </button>
    </form>
  );
}
