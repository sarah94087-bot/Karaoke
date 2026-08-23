import { notFound } from "next/navigation";

import { JobProgress } from "@/components/JobProgress";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/i18n/config";
import { type JobStatus, getJob } from "@/lib/api";
import { serverToken } from "@/lib/session-server";

/**
 * The progress screen.
 *
 * The first state is fetched on the server so the page arrives with something
 * on it. Without that there is a blank frame between navigation and the first
 * SSE message, which on a slow connection is exactly when the user is most
 * unsure whether anything is happening.
 */

export const dynamic = "force-dynamic";

export default async function JobPage({
  params,
}: {
  params: Promise<{ locale: string; jobId: string }>;
}) {
  const { locale, jobId } = await params;
  if (!isLocale(locale)) notFound();
  const t = await getDictionary(locale);

  let initial: JobStatus | null = null;
  try {
    initial = await getJob(jobId, await serverToken());
  } catch {
    // The client will try again over SSE, and show its own error if that fails
    // too. A server-side failure here should not blank the page.
  }

  return (
    <main>
      <h1>{t.progress.title}</h1>
      <JobProgress jobId={jobId} initial={initial} t={t} locale={locale} />
    </main>
  );
}
