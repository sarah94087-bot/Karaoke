"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import type { Dictionary } from "@/i18n";
import { type Session, adoptSessionFromUrl, currentSession, signOut } from "@/lib/auth";

/**
 * Who is signed in, and the way out (T-3.6).
 *
 * Rendered on every screen, so it is the one place that notices a session has
 * expired: `currentSession` refreshes a token that is close to running out and
 * returns null when the refresh token itself is spent, which is the same thing
 * as signed out and is shown as it.
 *
 * Until T-3.7 binds songs to their owner, being signed in changes nothing about
 * what the API returns - so this deliberately does not gate anything. Hiding
 * the library behind a sign-in that does not yet protect it would be theatre.
 */
export function AccountBar({ t, locale }: { t: Dictionary; locale: string }) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [known, setKnown] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // Before reading the cookie, not after: a confirmation link arrives with a
    // session in the fragment, and reading the cookie first would decide
    // "signed out" a moment before the session it was handed is stored.
    const fromLink = adoptSessionFromUrl();
    if (fromLink !== null) {
      setSession(fromLink.session);
      setKnown(true);
      if (fromLink.purpose === "recovery") {
        // A reset link, wherever it landed. Somebody who asked to change their
        // password has not asked to be signed in and taken to the library.
        router.replace(`/${locale}/reset/confirm`);
        return;
      }
      // The rest of the page was server-rendered for a visitor with no session.
      router.refresh();
      return;
    }
    void currentSession().then(({ session: found, refreshed }) => {
      if (cancelled) return;
      setSession(found);
      setKnown(true);
      // The same reasoning as the branch above, for the case nobody clicked a
      // link to get here: the token was renewed, so the page on the screen was
      // built with the one before it. If that one had expired, the server
      // asked the API with it, got a 401, and drew the signed-out screen for
      // somebody who is signed in. Redrawing costs one request and is the
      // difference between "opened the app in the morning and it had logged me
      // out" and not noticing anything at all.
      if (refreshed) router.refresh();
    });
    return () => {
      cancelled = true;
    };
  }, [router, locale]);

  // Nothing at all until the cookie has been read. A "sign in" link that
  // appears for half a second on every page load, for somebody who is already
  // signed in, reads as being logged out.
  if (!known) return <div className="account-bar" aria-hidden="true" />;

  return (
    <div className="account-bar">
      {session === null ? (
        <Link className="auth-link" href={`/${locale}/signin`}>
          {t.auth.signIn}
        </Link>
      ) : (
        <>
          <Link className="auth-link" href={`/${locale}/account`}>
            {t.nav.account}
          </Link>
          <span className="account-email" dir="ltr">
            {session.email}
          </span>
          <button
            type="button"
            className="link-button"
            onClick={async () => {
              await signOut();
              setSession(null);
              router.push(`/${locale}/signin`);
              router.refresh();
            }}
          >
            {t.auth.signOut}
          </button>
        </>
      )}
    </div>
  );
}
