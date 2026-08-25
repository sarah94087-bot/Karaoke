"use client";

import { useEffect } from "react";

/**
 * Registering the service worker (T-5.3). Renders nothing, like
 * `ErrorReporting` - it exists to run one line in the browser.
 *
 * **Not in development, deliberately.** `next dev` serves chunks that change
 * on every save, and a worker holding on to them turns an edit that did not
 * appear into a twenty-minute hunt through the wrong file. The worker is a
 * production concern and is checked against a production build.
 *
 * `updateViaCache: "none"` is the setting that decides whether a fix ever
 * arrives: without it the browser may serve `sw.js` itself from its ordinary
 * HTTP cache for up to a day, so a deployed worker keeps handing out the
 * previous one's rules.
 */
export function ServiceWorker() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;

    // Development does not merely skip registration, it undoes one. A worker
    // is scoped to an origin, and `next start` and `next dev` are the same
    // origin on this machine - so a worker left behind by a production check
    // goes on answering in development, from a cache of a different build.
    // That failure looks like an edit that did not take effect, which is a
    // long way to walk before suspecting a service worker.
    if (process.env.NODE_ENV !== "production") {
      void navigator.serviceWorker
        .getRegistrations()
        .then((workers) => Promise.all(workers.map((worker) => worker.unregister())))
        .catch(() => {});
      return;
    }

    navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" }).catch(() => {
      // An unsupported or blocked worker is not a failure worth a screen: the
      // app works exactly as it did before this task, only without the cache.
    });
  }, []);

  return null;
}
