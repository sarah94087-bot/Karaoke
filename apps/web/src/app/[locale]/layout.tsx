import type { Metadata, Viewport } from "next";
import { notFound } from "next/navigation";

import { AccountBar } from "@/components/AccountBar";
import { ErrorReporting } from "@/components/ErrorReporting";
import { ServiceWorker } from "@/components/ServiceWorker";
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
    // T-5.3. iOS ignores the manifest's icons and reads this link instead,
    // which is why the icon exists in two places rather than one.
    icons: { apple: "/apple-touch-icon.png" },
    // What iOS reads in place of `display: standalone`: without it, adding to
    // the home screen there opens Safari with its chrome, which is the thing
    // "install to the home screen" is meant to remove.
    appleWebApp: { capable: true, title: dictionary.app.name, statusBarStyle: "black-translucent" },
  };
}

/**
 * The colour a phone paints around the app - the address bar on Android, the
 * status bar in a standalone window. The same value as `globals.css`'s `--bg`,
 * so the frame and the page are one surface rather than two.
 */
export const viewport: Viewport = {
  themeColor: "#10101a",
};

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
        {/* Renders nothing; makes the app installable and fast to open (T-5.3). */}
        <ServiceWorker />
        {children}
      </body>
    </html>
  );
}
