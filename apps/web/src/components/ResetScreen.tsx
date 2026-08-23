"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AuthForm, AuthLink } from "@/components/AuthForm";
import type { Dictionary } from "@/i18n";
import { readCookie, requestPasswordReset, updatePassword } from "@/lib/auth";

/** Step one: ask for a link. */
export function ResetRequestScreen({ t, locale }: { t: Dictionary; locale: string }) {
  const [sentTo, setSentTo] = useState<string | null>(null);

  if (sentTo !== null) {
    return (
      <div className="card auth-form">
        <h2 className="auth-title">{t.auth.checkEmailTitle}</h2>
        <p>{t.auth.resetSent.replace("{email}", sentTo)}</p>
        <p className="hint">{t.auth.checkEmailHint}</p>
        <AuthLink href={`/${locale}/signin`}>{t.auth.backToSignIn}</AuthLink>
      </div>
    );
  }

  return (
    <AuthForm
      fields="email"
      submitLabel={t.auth.sendReset}
      busyLabel={t.auth.sending}
      t={t}
      onSubmit={async ({ email }) => {
        // Where the link comes back to. Absolute, because it is the address
        // Supabase redirects the browser to from the email.
        await requestPasswordReset(email, `${window.location.origin}/${locale}/reset/confirm`);
        setSentTo(email);
      }}
      footer={<AuthLink href={`/${locale}/signin`}>{t.auth.backToSignIn}</AuthLink>}
    >
      <p>{t.auth.resetExplain}</p>
    </AuthForm>
  );
}

/**
 * Step two: the screen the emailed link lands on.
 *
 * Supabase puts the token in the URL **fragment** (`#access_token=…`), which
 * never reaches a server - that is the point of it. So this screen has to be a
 * client component, and it has to read the fragment before React Router-style
 * navigation drops it.
 */
export function ResetConfirmScreen({ t, locale }: { t: Dictionary; locale: string }) {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const inUrl = fragment.get("access_token");
    if (inUrl) {
      // Taken out of the address bar once it is read: a recovery token in a URL
      // is a password, and it should not survive in history or in a link
      // somebody pastes to ask for help.
      window.history.replaceState(null, "", window.location.pathname);
      setToken(inUrl);
      setReady(true);
      return;
    }
    // Or the session the link already produced. Supabase redirects a recovery
    // link to the site root when the address is not in its allow-list, and the
    // account bar adopts it there and sends the browser here - by which point
    // the fragment is gone and the token lives in the cookie.
    setToken(readCookie()?.accessToken ?? null);
    setReady(true);
  }, []);

  if (!ready) return null;

  if (token === null) {
    return (
      <div className="card auth-form">
        <p className="song-error">{t.errors.reset_link_invalid}</p>
        <AuthLink href={`/${locale}/reset`}>{t.auth.sendReset}</AuthLink>
      </div>
    );
  }

  if (done) {
    return (
      <div className="card auth-form">
        <h2 className="auth-title">{t.auth.passwordChanged}</h2>
        <AuthLink href={`/${locale}`}>{t.auth.toLibrary}</AuthLink>
      </div>
    );
  }

  return (
    <AuthForm
      fields="password"
      submitLabel={t.auth.setPassword}
      busyLabel={t.auth.saving}
      t={t}
      onSubmit={async ({ password }) => {
        await updatePassword(token, password);
        setDone(true);
        router.refresh();
      }}
    >
      <p>{t.auth.setPasswordExplain}</p>
    </AuthForm>
  );
}
