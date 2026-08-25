import { notFound } from "next/navigation";

import { ImportForm } from "@/components/ImportForm";
import { UploadForm } from "@/components/UploadForm";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";
import { getFeatures } from "@/lib/api";
import { serverToken } from "@/lib/session-server";

/** Rendered per request: whether the import module is on is the deployment's
 *  answer, not something to bake into a build (T-4.1). */
export const dynamic = "force-dynamic";

export default async function Upload({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  // Asked on the server so the page arrives with the right number of forms on
  // it rather than growing one after it loads. `getFeatures` answers "off" if
  // the API cannot be reached, so the flag being off and the API being down
  // look the same here - which is the safe way round.
  const features = await getFeatures(await serverToken());

  return (
    <main>
      <h1>{t.upload.title}</h1>
      <UploadForm t={t} locale={locale} />
      {features.import_enabled ? (
        <>
          <p className="or-divider">{t.upload.or}</p>
          <ImportForm t={t} locale={locale} />
        </>
      ) : null}
    </main>
  );
}
