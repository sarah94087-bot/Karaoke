import { notFound } from "next/navigation";

import { SignInScreen } from "@/components/SignInScreen";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  return (
    <main className="auth-page">
      <h1>{t.auth.signInTitle}</h1>
      <SignInScreen t={t} locale={locale} />
    </main>
  );
}
