"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { Dictionary } from "@/i18n";
import { type JobStatus, getJob, jobEventsUrl, retryJob } from "@/lib/api";
import { readCookie } from "@/lib/auth";
import { type SongState, errorText, stateLabel } from "@/lib/song";
import { readEventStream } from "@/lib/sse";

/**
 * The progress screen: chapter 8's "live stages in Hebrew, and a button that
 * lights up at PLAYABLE".
 *
 * Live over SSE (D-18) with polling as the fallback. The fallback is not
 * decoration: a streamed response is the first thing a corporate proxy breaks,
 * and a progress bar that silently stops is indistinguishable from a job that
 * hung. It also proved to be load-bearing - between T-3.7 and T-3.11 the
 * stream could not authenticate at all and this fallback was carrying every
 * job on its own, which is why nothing looked broken. `lib/sse.ts` has that
 * story; the short version is that the stream now travels by `fetch`, with the
 * token in a header where it belongs.
 */

const POLL_MS = 2000;

type Connection = "live" | "polling" | "closed";

export function JobProgress({
  jobId,
  initial,
  t,
  locale,
}: {
  jobId: string;
  initial: JobStatus | null;
  t: Dictionary;
  locale: string;
}) {
  const [job, setJob] = useState<JobStatus | null>(initial);
  const [connection, setConnection] = useState<Connection>("live");
  const [retrying, setRetrying] = useState(false);

  const finished = job !== null && (job.state === "ready" || job.state === "failed");

  useEffect(() => {
    if (finished) {
      setConnection("closed");
      return;
    }

    let stopped = false;
    let poller: ReturnType<typeof setInterval> | undefined;
    const abort = new AbortController();

    const apply = (raw: string) => {
      const data = JSON.parse(raw) as {
        state: JobStatus["state"];
        current_step: JobStatus["current_step"];
        progress: number;
        is_playable: boolean;
        error_code: string | null;
        song_id: string | null;
      };
      setJob((previous) => ({
        id: jobId,
        attempts: previous?.attempts ?? 1,
        gpu_seconds: previous?.gpu_seconds ?? null,
        ...data,
        song_id: data.song_id ?? previous?.song_id ?? "",
      }));
    };

    const startPolling = () => {
      // The stream is gone, whatever the reason - a proxy that breaks
      // streaming, a dropped connection, a token the API no longer accepts.
      // From here the screen is driven by asking, which is slower and works
      // everywhere. This is T-1.11's fallback and it is now the *only*
      // recovery, because a hand-read stream does not reconnect by itself.
      if (stopped) return;
      setConnection("polling");
      poller ??= setInterval(() => {
        getJob(jobId)
          .then(setJob)
          .catch(() => {
            /* keep trying; the next tick may succeed */
          });
      }, POLL_MS);
    };

    // `readEventStream` and not `EventSource`: the endpoint needs a bearer
    // token and EventSource cannot send one. See `lib/sse.ts`.
    readEventStream(jobEventsUrl(jobId), {
      token: readCookie()?.accessToken,
      signal: abort.signal,
      onMessage: ({ event, data }) => {
        if (["snapshot", "progress", "playable", "ready", "failed"].includes(event)) apply(data);
      },
    })
      // The server closes the stream when the job finishes, and by then the
      // `ready` or `failed` message has already been applied - so a clean end
      // is not a reason to start polling.
      .catch(startPolling);

    return () => {
      stopped = true;
      abort.abort();
      if (poller !== undefined) clearInterval(poller);
    };
  }, [jobId, finished]);

  if (job === null) {
    return <p className="hint">{t.progress.waiting}</p>;
  }

  const asSong: SongState = {
    status: "processing",
    job: {
      id: job.id,
      state: job.state,
      current_step: job.current_step,
      progress: job.progress,
      error_code: job.error_code,
    },
  };
  const label = stateLabel(asSong, t);

  return (
    <div className="progress-screen">
      <p className="step" aria-live="polite">
        {job.state === "failed" ? t.progress.failedTitle : label}
      </p>

      <div
        className="progress progress-large"
        role="progressbar"
        aria-valuenow={job.progress}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="progress-fill" style={{ inlineSize: `${job.progress}%` }} />
      </div>

      {/* D-28: this is the whole point. The button appears the moment the stems
          are encoded, well before the job is finished. */}
      {job.is_playable && job.song_id ? (
        <Link className="playable-banner" href={`/${locale}/songs/${job.song_id}`}>
          <strong>{t.progress.sing}</strong>
          <span>{t.job.playableHint}</span>
        </Link>
      ) : null}

      {job.state === "failed" ? (
        <div className="card card-error">
          <p>{errorText(asSong, t)}</p>
          <button
            type="button"
            disabled={retrying}
            onClick={() => {
              setRetrying(true);
              retryJob(jobId)
                .then((updated) => {
                  setJob(updated);
                  setConnection("live");
                })
                .finally(() => setRetrying(false));
            }}
          >
            {t.job.retry}
          </button>
        </div>
      ) : null}

      {job.state === "ready" ? (
        <p className="done">
          <strong>{t.progress.done}</strong>
        </p>
      ) : null}

      {connection === "polling" ? <p className="hint">{t.progress.reconnecting}</p> : null}

      <p>
        <Link href={`/${locale}`}>{t.progress.toLibrary}</Link>
      </p>
    </div>
  );
}
