import type { Metadata } from "next";
import { notFound } from "next/navigation";

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

  return (
    <html lang={locale} dir={directionOf(locale)}>
      <body>{children}</body>
    </html>
  );
}
