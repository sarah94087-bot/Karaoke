import { notFound } from "next/navigation";

import { AccountScreen } from "@/components/AccountScreen";
import { SignedOut } from "@/components/SignedOut";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";
import { ApiError, getQuota } from "@/lib/api";
import { SESSION_COOKIE, decodeSession } from "@/lib/auth";
import { cookies } from "next/headers";

export const dynamic = "force-dynamic";

export default async function Account({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  const session = decodeSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (session === null) return <SignedOut t={t} locale={locale} title={t.account.title} />;

  try {
    const quota = await getQuota(session.accessToken);
    return (
      <main>
        <h1>{t.account.title}</h1>
        <AccountScreen quota={quota} email={session.email} t={t} locale={locale} />
      </main>
    );
  } catch (error) {
    const code = error instanceof ApiError ? error.code : "unknown";
    if (code === "not_signed_in") return <SignedOut t={t} locale={locale} title={t.account.title} />;
    return (
      <main>
        <h1>{t.account.title}</h1>
        <div className="card card-error">
          <p>{t.errors[code as keyof typeof t.errors] ?? t.errors.unknown}</p>
        </div>
      </main>
    );
  }
}
