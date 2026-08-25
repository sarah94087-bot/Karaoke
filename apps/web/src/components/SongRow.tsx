import Link from "next/link";

import { QueueButton } from "@/components/QueueControls";
import type { Dictionary } from "@/i18n";
import type { LibrarySong } from "@/lib/api";
import { errorText, formatDuration, stateLabel } from "@/lib/song";

/**
 * One song in the library.
 *
 * The rule this component exists to enforce: a song that is playable says so,
 * even while it is still being processed. Showing only `status` would collapse
 * D-28 back into a single "processing" state and lose the thing that makes the
 * wait bearable.
 */

export function SongRow({
  song,
  t,
  locale,
}: {
  song: LibrarySong;
  t: Dictionary;
  locale: string;
}) {
  const length = formatDuration(song.duration_sec);
  const failed = song.job?.state === "failed";
  const running = song.job?.state === "running" || song.job?.state === "queued";

  return (
    <li className="song" data-state={song.job?.state ?? song.status}>
      <div className="song-main">
        {/* A playable song opens the player; one still working opens its
            progress screen. Either way the row goes somewhere - a library whose
            rows do nothing is a list, not a library. */}
        {song.is_playable ? (
          <Link className="song-title" href={`/${locale}/songs/${song.id}`}>
            {song.title}
          </Link>
        ) : song.job === null ? (
          <span className="song-title">{song.title}</span>
        ) : (
          <Link className="song-title" href={`/${locale}/jobs/${song.job.id}`}>
            {song.title}
          </Link>
        )}
        {song.artist ? <span className="song-artist">{song.artist}</span> : null}
        {length ? <span className="song-duration">{length}</span> : null}
      </div>

      <div className="song-state">
        {/* T-5.1. Only on a song that can actually be sung: queueing one that
            is still separating would put a wait in the middle of an evening. */}
        {song.is_playable ? <QueueButton song={{ id: song.id, title: song.title }} t={t} /> : null}
        {song.is_playable ? <span className="badge badge-playable">{t.job.playable}</span> : null}
        <span className="badge">{stateLabel(song, t)}</span>
      </div>

      {running ? (
        <div
          className="progress"
          role="progressbar"
          aria-valuenow={song.job?.progress ?? 0}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="progress-fill" style={{ inlineSize: `${song.job?.progress ?? 0}%` }} />
        </div>
      ) : null}

      {failed ? <p className="song-error">{errorText(song, t)}</p> : null}
    </li>
  );
}
