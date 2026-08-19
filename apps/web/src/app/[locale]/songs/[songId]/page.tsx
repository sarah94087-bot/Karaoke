import Link from "next/link";
import { notFound } from "next/navigation";

import { Player } from "@/components/Player";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";
import { ApiError, getSong } from "@/lib/api";

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

    return (
      <main>
        <header className="page-header">
          <h1>{song.title}</h1>
          <Link className="button-link" href={`/${locale}`}>
            {t.progress.toLibrary}
          </Link>
        </header>
        {song.artist ? <p className="tagline">{song.artist}</p> : null}
        <Player song={song} t={t} />
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
