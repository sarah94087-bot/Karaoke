"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import Link from "next/link";

import type { Dictionary } from "@/i18n";
import { ApiError, importSong } from "@/lib/api";

/**
 * The other way to add a song (D-01, T-4.1): a link instead of a file.
 *
 * Rendered only when the deployment says the module is on - the upload page
 * asks `/system/features` on the server, so a visitor to an installation with
 * the flag off never sees this at all. That is what "switching it off hides the
 * feature without breaking anything" has to mean on the screen: not a form that
 * explains it is disabled, but no form.
 *
 * There is no progress bar here, deliberately. The upload form has one because
 * the browser is sending the bytes and knows how many have gone; here the API
 * is fetching them from somewhere else and the browser knows nothing until it
 * is done. A bar that could only be a guess would be worse than a sentence
 * saying what is happening.
 */
export function ImportForm({ t, locale }: { t: Dictionary; locale: string }) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAccount, setShowAccount] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || url.trim() === "") return;

    setError(null);
    setBusy(true);
    try {
      const result = await importSong(url.trim());
      if (result.job_id === null) {
        // The same audio was already here - the deduplication is on the
        // normalised bytes, so a link to a song already uploaded from a file
        // lands here too.
        router.push(`/${locale}?existing=${result.id}`);
        return;
      }
      router.push(`/${locale}/jobs/${result.job_id}`);
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "unknown";
      setError(t.errors[code as keyof typeof t.errors] ?? t.errors.unknown);
      setShowAccount(["storage_full", "monthly_songs_exhausted"].includes(code));
      setBusy(false);
    }
  }

  return (
    <form className="card upload" onSubmit={submit}>
      <h2>{t.upload.importTitle}</h2>

      <label className="field">
        <span>{t.upload.importLabel}</span>
        <input
          type="url"
          // The address is Latin even on a Hebrew page, and a right-to-left
          // input turns `https://…/song.mp3` into something unreadable. Same
          // reasoning as the durations in the library rows (T-1.10).
          dir="ltr"
          inputMode="url"
          placeholder="https://"
          value={url}
          disabled={busy}
          onChange={(event) => {
            setUrl(event.target.value);
            setError(null);
          }}
        />
      </label>

      <p className="hint">{t.upload.importHint}</p>

      {busy ? <p className="progress-line">{t.upload.importWorking}</p> : null}
      {error ? <p className="song-error">{error}</p> : null}
      {showAccount ? (
        <Link className="auth-link" href={`/${locale}/account`}>
          {t.account.freeUpTitle}
        </Link>
      ) : null}

      <button type="submit" disabled={busy || url.trim() === ""}>
        {busy ? t.upload.importWorking : t.upload.importSubmit}
      </button>
    </form>
  );
}
