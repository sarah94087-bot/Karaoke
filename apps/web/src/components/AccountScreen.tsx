"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import type { Dictionary } from "@/i18n";
import { type Quota, deleteSong } from "@/lib/api";

function megabytes(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0);
}

/**
 * The account screen (T-3.8): what is used, what is left, and the way to free
 * some.
 *
 * D-30 asks for the offer to be part of the message, so the songs to remove are
 * on this screen whether or not anything is full - somebody who can see the
 * storage filling up should not have to wait until an upload is refused before
 * being shown what to do about it.
 */
export function AccountScreen({
  quota,
  email,
  t,
  locale,
}: {
  quota: Quota;
  email: string | null;
  t: Dictionary;
  locale: string;
}) {
  const router = useRouter();
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const storageUsed = (quota.storage_bytes / quota.storage_limit_bytes) * 100;
  const songsUsed = (quota.songs_this_month / quota.songs_per_month) * 100;

  async function remove(songId: string) {
    setRemoving(songId);
    setError(null);
    try {
      await deleteSong(songId);
      // The numbers on this page came from the server; they are stale the
      // moment a song goes.
      router.refresh();
    } catch {
      setError(t.errors.unknown);
    } finally {
      setRemoving(null);
    }
  }

  return (
    <div className="account">
      {email ? (
        <p className="tagline" dir="ltr">
          {email}
        </p>
      ) : null}

      <div className="card">
        <h2 className="auth-title">{t.account.songsThisMonth}</h2>
        <div className="progress">
          <div className="progress-fill" style={{ inlineSize: `${Math.min(100, songsUsed)}%` }} />
        </div>
        <p className="progress-line">
          <span className="ltr-number">
            {quota.songs_this_month} / {quota.songs_per_month}
          </span>{" "}
          {t.account.songsLeft.replace("{n}", String(quota.songs_left))}
        </p>
      </div>

      <div className="card">
        <h2 className="auth-title">{t.account.storage}</h2>
        <div className="progress">
          <div className="progress-fill" style={{ inlineSize: `${Math.min(100, storageUsed)}%` }} />
        </div>
        <p className="progress-line">
          <span className="ltr-number">
            {megabytes(quota.storage_bytes)} / {megabytes(quota.storage_limit_bytes)} MB
          </span>
        </p>
      </div>

      {quota.candidates.length > 0 ? (
        <div className="card">
          <h2 className="auth-title">{t.account.freeUpTitle}</h2>
          {/* D-30: the least played first, because those are the ones whose
              deletion costs their owner least. */}
          <p className="hint">{t.account.freeUpHint}</p>
          <ul className="candidates">
            {quota.candidates.map((candidate) => (
              <li key={candidate.song_id}>
                <div className="candidate-song">
                  <Link href={`/${locale}/songs/${candidate.song_id}`}>{candidate.title}</Link>
                  <span className="hint">
                    <span className="ltr-number">{megabytes(candidate.bytes)} MB</span>
                    {candidate.last_played_at === null ? ` · ${t.account.neverPlayed}` : ""}
                  </span>
                </div>
                <button
                  type="button"
                  className="link-button"
                  disabled={removing !== null}
                  onClick={() => void remove(candidate.song_id)}
                >
                  {removing === candidate.song_id ? t.account.removing : t.account.remove}
                </button>
              </li>
            ))}
          </ul>
          {error ? <p className="song-error">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
