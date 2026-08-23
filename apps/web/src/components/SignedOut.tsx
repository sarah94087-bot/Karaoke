import Link from "next/link";

import type { Dictionary } from "@/i18n";

/**
 * Signed out, which is a state and not a failure (T-3.7).
 *
 * Shown in two situations that look different from the code and identical from
 * the outside: no session at all, and a session the API has stopped accepting -
 * revoked, expired, or from before a password change. A live check produced the
 * second one and got a red error card with a request id on it, which is the
 * wrong shape of answer to "you need to sign in".
 */
export function SignedOut({ t, locale, title }: { t: Dictionary; locale: string; title: string }) {
  return (
    <main>
      <h1>{title}</h1>
      <div className="card">
        <p>{t.library.signedOut}</p>
        <Link className="button-link" href={`/${locale}/signin`}>
          {t.auth.signIn}
        </Link>
      </div>
    </main>
  );
}
