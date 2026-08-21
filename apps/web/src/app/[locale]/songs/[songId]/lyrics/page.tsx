import { notFound } from "next/navigation";

import { LyricsEditor } from "@/components/LyricsEditor";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";
import { ApiError, type SongLyrics, getLyrics, getSong, isPending } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The lyrics editor (T-2.8).
 *
 * `?version=` opens an older set, which is how "back to what the machine wrote"
 * works: read the version, then save it, which - since a save creates a version
 * rather than overwriting one (chapter 6) - leaves the correction that was
 * abandoned still sitting there behind it.
 */
export default async function LyricsPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string; songId: string }>;
  searchParams: Promise<{ version?: string }>;
}) {
  const { locale, songId } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);
  const asked = Number((await searchParams).version);
  const version = Number.isFinite(asked) && asked > 0 ? asked : undefined;

  try {
    const [song, lyrics] = await Promise.all([getSong(songId), getLyrics(songId, version)]);

    // A song still being transcribed has nothing to edit yet, and D-28 says
    // that is a normal state rather than an error: the editor opens on an empty
    // list and the player is where the waiting is done.
    const editable: SongLyrics = isPending(lyrics)
      ? {
          song_id: songId,
          version: 0,
          language: "he",
          source: "manual",
          is_verified: false,
          status: "pending",
          lines: [],
          versions: [],
          created_at: new Date().toISOString(),
        }
      : lyrics;

    return (
      <main>
        <LyricsEditor
          songId={songId}
          songTitle={song.title}
          lyrics={editable}
          locale={locale}
          t={t}
        />
      </main>
    );
  } catch (error) {
    const code = error instanceof ApiError ? error.code : "unknown";
    return (
      <main>
        <h1>{t.editor.title}</h1>
        <div className="card card-error">
          <p>{t.errors[code as keyof typeof t.errors] ?? t.errors.unknown}</p>
        </div>
      </main>
    );
  }
}
