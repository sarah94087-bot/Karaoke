import Link from "next/link";
import { notFound } from "next/navigation";

import { Player } from "@/components/Player";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";
import { ApiError, getLyrics, getSong, isPending } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function SongPage({
  params,
}: {
  params: Promise<{ locale: string; songId: string }>;
}) {
  const { locale, songId } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  try {
    const song = await getSong(songId);
    // Alongside the song rather than after it: the words are what this screen
    // is for, and a client fetch would show the "on the way" state for a moment
    // on a song whose lyrics have been sitting in the database for a week.
    // Failure is fine - the player asks again itself, and keeps asking while
    // the pipeline is still working (D-28).
    const lyrics = await getLyrics(songId).catch(() => null);

    return (
      <main>
        <header className="page-header">
          <h1>{song.title}</h1>
          <Link className="button-link" href={`/${locale}`}>
            {t.progress.toLibrary}
          </Link>
        </header>
        {song.artist ? <p className="tagline">{song.artist}</p> : null}

        {/*
          T-1.15: measured during processing and stored on the song. Shown next
          to the controls that change them, so "+2 from D" is readable as one
          thought rather than two.
        */}
        {song.original_key !== null || song.bpm !== null ? (
          <dl className="song-facts">
            {song.original_key !== null ? (
              <div>
                <dt>{t.song.originalKey}</dt>
                <dd className="fact-value">{song.original_key}</dd>
              </div>
            ) : null}
            {song.bpm !== null ? (
              <div>
                <dt>{t.song.bpm}</dt>
                <dd className="fact-value">
                  {Math.round(song.bpm)} <span className="fact-unit">{t.song.bpmUnit}</span>
                </dd>
              </div>
            ) : null}
          </dl>
        ) : null}
        <Player
          song={song}
          lyrics={lyrics === null || isPending(lyrics) ? null : lyrics.lines}
          t={t}
        />
      </main>
    );
  } catch (error) {
    const code = error instanceof ApiError ? error.code : "unknown";
    return (
      <main>
        <h1>{t.library.title}</h1>
        <div className="card card-error">
          <p>{t.errors[code as keyof typeof t.errors] ?? t.errors.unknown}</p>
        </div>
      </main>
    );
  }
}
