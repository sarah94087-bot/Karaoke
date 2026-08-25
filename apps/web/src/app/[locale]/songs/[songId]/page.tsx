import Link from "next/link";
import { notFound } from "next/navigation";

import { Player } from "@/components/Player";
import { SongFacts } from "@/components/SongFacts";
import { SongTitle } from "@/components/SongTitle";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";
import { ApiError, getLyrics, getSong, isPending } from "@/lib/api";
import { SignedOut } from "@/components/SignedOut";
import { serverToken } from "@/lib/session-server";

export const dynamic = "force-dynamic";

export default async function SongPage({
  params,
}: {
  params: Promise<{ locale: string; songId: string }>;
}) {
  const { locale, songId } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  const token = await serverToken();

  try {
    const song = await getSong(songId, token);
    // Alongside the song rather than after it: the words are what this screen
    // is for, and a client fetch would show the "on the way" state for a moment
    // on a song whose lyrics have been sitting in the database for a week.
    // Failure is fine - the player asks again itself, and keeps asking while
    // the pipeline is still working (D-28).
    const lyrics = await getLyrics(songId, undefined, token).catch(() => null);

    return (
      <main>
        <header className="page-header">
          {/* T-4.2: the name and the artist are filled automatically and are
              editable right here, because here is where somebody notices they
              are wrong. */}
          <SongTitle songId={songId} title={song.title} artist={song.artist} t={t} />
          <div className="header-actions">
            {/* The editor is the other half of phase 2: phase 0 measured that
                automatic Hebrew alignment will not be good enough to leave
                alone, so getting to it has to be one click from the song. */}
            <Link className="button-link" href={`/${locale}/songs/${songId}/lyrics`}>
              {t.editor.open}
            </Link>
            <Link className="button-link" href={`/${locale}`}>
              {t.progress.toLibrary}
            </Link>
          </div>
        </header>

        {/*
          T-1.15: measured during processing and stored on the song. Shown next
          to the controls that change them, so "+2 from D" is readable as one
          thought rather than two. A client component since T-3.5, because the
          measurement now happens *after* the player opens and these numbers
          have to arrive on their own.
        */}
        <SongFacts song={song} t={t} />
        <Player
          song={song}
          lyrics={lyrics === null || isPending(lyrics) ? null : lyrics.lines}
          t={t}
        />
      </main>
    );
  } catch (error) {
    const code = error instanceof ApiError ? error.code : "unknown";
    if (code === "not_signed_in") return <SignedOut t={t} locale={locale} title={t.app.name} />;
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
