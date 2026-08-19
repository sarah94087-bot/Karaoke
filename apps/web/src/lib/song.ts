/**
 * The decisions a library row makes, kept out of the component so they can be
 * tested without rendering anything.
 */

import type { Dictionary } from "@/i18n";
import type { LibrarySong, SongJob } from "@/lib/api";

/**
 * The only part of a song these helpers need.
 *
 * Narrower than `LibrarySong` on purpose: the progress screen has a job and a
 * state but no title, artist or duration, and widening it there would mean
 * inventing fields to satisfy a type.
 */
export interface SongState {
  status: LibrarySong["status"];
  job: SongJob | null;
}

export function formatDuration(seconds: number | null): string | null {
  if (seconds === null || seconds < 0) return null;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * What the badge says.
 *
 * A running job shows its *step* rather than the word "processing": chapter 8
 * asks for the live stages by name, and "separating stems" tells a waiting user
 * something that "processing" does not.
 */
export function stateLabel(song: SongState, t: Dictionary): string {
  if (song.job === null) return t.job.state[song.status === "ready" ? "ready" : "queued"];
  if (song.job.state === "running" && song.job.current_step !== null) {
    return t.job.step[song.job.current_step];
  }
  return t.job.state[song.job.state];
}

/**
 * The Hebrew for a failure code, or a sentence for a code we have not
 * translated yet. A new code always reaches production before its translation.
 */
export function errorText(song: Pick<SongState, "job">, t: Dictionary): string | null {
  const code = song.job?.error_code;
  if (!code) return null;
  return t.errors[code as keyof typeof t.errors] ?? t.errors.unknown;
}
