import { notFound } from "next/navigation";

import { isLocale } from "@/i18n/config";
import { getDictionary } from "@/i18n";

/**
 * The first screen. Every string comes from the dictionary - none are written
 * into the component - which is the property T-1.9 is actually delivering.
 */
export default async function Home({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  return (
    <main>
      <h1>{t.home.title}</h1>
      <p className="tagline">{t.app.tagline}</p>
      <div className="card">
        <p>{t.home.intro}</p>
        <p>{t.home.next}</p>
      </div>
    </main>
  );
}
