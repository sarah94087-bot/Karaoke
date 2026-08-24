import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { AccountBar } from "@/components/AccountBar";
import { ErrorReporting } from "@/components/ErrorReporting";
import { directionOf, isLocale, locales } from "@/i18n/config";
import { getDictionary } from "@/i18n";

import "../globals.css";

/**
 * The document element, where language and direction actually belong.
 *
 * `dir` is set from the locale rather than hardcoded to rtl, even though
 * Hebrew is the only language a user will see today. Hardcoding it would make
 * the second language a rewrite instead of a dictionary file, which is exactly
 * what D-20 ("i18n from the first day") exists to prevent.
 */

export async function generateStaticParams() {
  return locales.map((locale) => ({ locale }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dictionary = await getDictionary(locale);
  return {
    title: dictionary.app.name,
    description: dictionary.app.tagline,
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = await getDictionary(locale);

  return (
    <html lang={locale} dir={directionOf(locale)}>
      <body>
        {/* On every screen, because it is also what notices a session has
            expired - see AccountBar. */}
        <AccountBar t={t} locale={locale} />
        {/* Renders nothing; listens for the errors nobody predicted (D-24). */}
        <ErrorReporting />
        {children}
      </body>
    </html>
  );
}
