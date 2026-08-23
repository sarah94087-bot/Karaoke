"use client";

import Link from "next/link";
import { useState } from "react";

import type { Dictionary } from "@/i18n";
import { AuthError } from "@/lib/auth";

export type AuthFields = "email" | "email+password" | "password";

/**
 * The one form behind all four account screens (T-3.6).
 *
 * Sign in, sign up, ask for a reset link and set a new password differ in which
 * fields they show and what happens on submit; everything else - the busy
 * state, the Hebrew error, the RTL layout, the 44px targets - is the same on
 * all four, and four copies of it would drift apart by the second bug fix.
 */
export function AuthForm({
  fields,
  submitLabel,
  busyLabel,
  onSubmit,
  t,
  children,
  footer,
}: {
  fields: AuthFields;
  submitLabel: string;
  busyLabel: string;
  onSubmit: (values: { email: string; password: string }) => Promise<void>;
  t: Dictionary;
  /** Anything shown above the fields - an explanation, or a success notice. */
  children?: React.ReactNode;
  footer?: React.ReactNode;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wantsEmail = fields !== "password";
  const wantsPassword = fields !== "email";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ email: email.trim(), password });
    } catch (caught) {
      const code = caught instanceof AuthError ? caught.code : "auth_failed";
      setError(t.errors[code as keyof typeof t.errors] ?? t.errors.auth_failed);
      setBusy(false);
    }
  }

  return (
    <form className="card auth-form" onSubmit={submit}>
      {children}

      {wantsEmail ? (
        <label className="field">
          <span>{t.auth.email}</span>
          <input
            type="email"
            autoComplete="email"
            required
            dir="ltr"
            disabled={busy}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
      ) : null}

      {wantsPassword ? (
        <label className="field">
          <span>{t.auth.password}</span>
          <input
            type="password"
            // "new-password" on the screens that set one, so a password manager
            // offers to generate rather than to fill.
            autoComplete={fields === "email+password" ? "current-password" : "new-password"}
            required
            minLength={8}
            dir="ltr"
            disabled={busy}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
      ) : null}

      {error ? <p className="song-error">{error}</p> : null}

      <button type="submit" disabled={busy}>
        {busy ? busyLabel : submitLabel}
      </button>

      {footer ? <div className="auth-footer">{footer}</div> : null}
    </form>
  );
}

/** The small print under a form: "no account yet?" and friends. */
export function AuthLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link className="auth-link" href={href}>
      {children}
    </Link>
  );
}
