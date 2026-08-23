"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthForm, AuthLink } from "@/components/AuthForm";
import type { Dictionary } from "@/i18n";
import { signUp } from "@/lib/auth";

/**
 * Creating an account.
 *
 * The project has email confirmation on, so submitting this usually does *not*
 * sign anyone in - it sends a link. A form that appears to do nothing is how
 * people decide an app is broken, so the screen says plainly that the next step
 * is in their inbox.
 */
export function SignUpScreen({ t, locale }: { t: Dictionary; locale: string }) {
  const router = useRouter();
  const [sentTo, setSentTo] = useState<string | null>(null);

  if (sentTo !== null) {
    return (
      <div className="card auth-form">
        <h2 className="auth-title">{t.auth.checkEmailTitle}</h2>
        <p>{t.auth.checkEmail.replace("{email}", sentTo)}</p>
        <p className="hint">{t.auth.checkEmailHint}</p>
        <AuthLink href={`/${locale}/signin`}>{t.auth.backToSignIn}</AuthLink>
      </div>
    );
  }

  return (
    <AuthForm
      fields="email+password"
      submitLabel={t.auth.signUp}
      busyLabel={t.auth.signingUp}
      t={t}
      onSubmit={async ({ email, password }) => {
        const { confirmed } = await signUp(email, password);
        if (confirmed) {
          // Only when the project has confirmation turned off. Then the account
          // is live and there is nothing to wait for.
          router.push(`/${locale}`);
          router.refresh();
          return;
        }
        setSentTo(email);
      }}
      footer={<AuthLink href={`/${locale}/signin`}>{t.auth.haveAccount}</AuthLink>}
    />
  );
}
