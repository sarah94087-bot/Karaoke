import { notFound } from "next/navigation";

import { UploadForm } from "@/components/UploadForm";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";

export default async function Upload({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  return (
    <main>
      <h1>{t.upload.title}</h1>
      <UploadForm t={t} locale={locale} />
    </main>
  );
}
