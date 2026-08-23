import Link from "next/link";
import { notFound } from "next/navigation";

import { SongRow } from "@/components/SongRow";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";
import { ApiError, getLibrary } from "@/lib/api";
import { SignedOut } from "@/components/SignedOut";
import { serverToken } from "@/lib/session-server";

/**
 * The library - chapter 8's first screen, and the app's home.
 *
 * Rendered on the server at request time, not cached: a song's state changes
 * while nobody is looking, and a cached page would show "processing" for a song
 * that finished ten minutes ago.
 */

export const dynamic = "force-dynamic";

export default async function Library({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  const token = await serverToken();
  // Not an error state: nobody is signed in yet, and the honest thing to show
  // is the way in rather than a library that would be empty for a reason the
  // visitor cannot see.
  if (token === null) return <SignedOut t={t} locale={locale} title={t.library.title} />;

  try {
    const library = await getLibrary(token);

    return (
      <main>
        <header className="page-header">
          <h1>{t.library.title}</h1>
          <Link className="button-link" href={`/${locale}/upload`}>
            {t.nav.upload}
          </Link>
        </header>
        <p className="tagline">{t.app.tagline}</p>

        {library.songs.length === 0 ? (
          <div className="card">
            <p>{t.library.empty}</p>
            <p>{t.library.emptyHint}</p>
          </div>
        ) : (
          <ul className="songs">
            {library.songs.map((song) => (
              <SongRow key={song.id} song={song} t={t} locale={locale} />
            ))}
          </ul>
        )}
      </main>
    );
  } catch (error) {
    // The API being down is a normal thing for a screen to survive, not a
    // reason to show a stack trace. The code carries through to Hebrew.
    const code = error instanceof ApiError ? error.code : "unknown";
    // A cookie the API no longer accepts - revoked, expired, or from before a
    // password change - is being signed out, not a failure. It arrives here
    // rather than above because the cookie exists; only the API can say the
    // token behind it is dead.
    if (code === "not_signed_in") {
      return <SignedOut t={t} locale={locale} title={t.library.title} />;
    }
    const message = t.errors[code as keyof typeof t.errors] ?? t.errors.unknown;

    return (
      <main>
        <h1>{t.library.title}</h1>
        <div className="card card-error">
          <p>{message}</p>
          {error instanceof ApiError && error.requestId ? (
            <p className="request-id">
              {t.library.requestId}: <code>{error.requestId}</code>
            </p>
          ) : null}
        </div>
      </main>
    );
  }
}
