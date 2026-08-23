"use client";

import { useRouter } from "next/navigation";

import { AuthForm, AuthLink } from "@/components/AuthForm";
import type { Dictionary } from "@/i18n";
import { signIn } from "@/lib/auth";

export function SignInScreen({ t, locale }: { t: Dictionary; locale: string }) {
  const router = useRouter();

  return (
    <AuthForm
      fields="email+password"
      submitLabel={t.auth.signIn}
      busyLabel={t.auth.signingIn}
      t={t}
      onSubmit={async ({ email, password }) => {
        await signIn(email, password);
        // `refresh()` as well as `push()`: the library is server-rendered, and
        // without this it would be re-shown from the cache it was built with
        // before there was anybody signed in.
        router.push(`/${locale}`);
        router.refresh();
      }}
      footer={
        <>
          <AuthLink href={`/${locale}/signup`}>{t.auth.noAccount}</AuthLink>
          <AuthLink href={`/${locale}/reset`}>{t.auth.forgot}</AuthLink>
        </>
      }
    />
  );
}
