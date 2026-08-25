/**
 * The service worker (T-5.3): what makes the app open fast, and installable.
 *
 * Hand-written, in `public/` and outside the bundle, for the same reason
 * `pitch-worklet.js` is: this file is registered by URL and never imported, so
 * there is nothing for a bundler to do. It is also why the project has no PWA
 * plugin - the whole of what one would generate is below, and the part that
 * matters is the part a plugin would get wrong.
 *
 * ## What is never cached, and why that is the important half
 *
 * Three kinds of request must always reach the network, and all three are
 * cross-origin, which is what makes the rule simple to state and hard to get
 * wrong:
 *
 *  - **the API** (`karuki-api.onrender.com`). The library changes whenever a
 *    job finishes, and a cached `401` from an expired session would lock
 *    somebody out of their own songs until they cleared storage.
 *  - **the bucket** (B2). Stem links are signed and expire in an hour
 *    (T-3.1); caching one would serve a link that no longer works, and each
 *    stem is megabytes against a quota.
 *  - **anything else with another origin** - Supabase, Sentry. None of it is
 *    ours to hold.
 *
 * So: same-origin GET only, and within that, two strategies.
 */

const VERSION = "v1";
const CACHE = `karuki-${VERSION}`;
const OFFLINE_PAGE = "/offline.html";

/** Enough to open the app, and nothing that changes per person. */
const PRECACHE = [OFFLINE_PAGE, "/icon-192.png", "/icon-512.png"];

/**
 * What to do with one request.
 *
 * Pure, and exported below, because this is the whole of the worker's
 * judgement and every branch of it is a decision worth testing: a wrong answer
 * here is a stale app or a leaked credential, and neither shows up as an error.
 *
 * - `"network"` - not ours to touch. Straight through, never stored.
 * - `"immutable"` - a content-hashed build asset. The file name changes when
 *   the file does, so cache-first is safe forever and is where "opens fast"
 *   actually comes from.
 * - `"fresh"` - ours, but not content-hashed: the worklet, the icons, the
 *   manifest, and every page. Network first so a deploy is picked up
 *   immediately, cache second so a bad connection still opens the app.
 */
function strategyFor(request, selfOrigin) {
  if (request.method !== "GET") return "network";

  const url = new URL(request.url);
  if (url.origin !== selfOrigin) return "network";

  // The two above are cross-origin *today*, and that is a deployment fact
  // rather than a promise: chapter 11 keeps everything runnable on one
  // machine, where the API can share this origin and hand out signed links
  // under `/api/v1/files/...` (T-3.1). Storing those would put whole stems in
  // a cache under a URL that stops working in an hour.
  if (url.pathname.startsWith("/api/")) return "network";
  if (url.searchParams.has("sig") || url.searchParams.has("X-Amz-Signature")) return "network";

  // Next's own build output is content-hashed and may be pinned. Everything
  // else of ours may change under the same name.
  if (url.pathname.startsWith("/_next/static/")) return "immutable";

  return "fresh";
}

async function fromCacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

async function fromNetworkFirst(request) {
  try {
    const response = await fetch(request);
    // Only a plain success is worth keeping. A redirect or an error page
    // cached under a URL somebody will come back to is a bug that outlives
    // the deploy that caused it.
    if (response.ok && response.type === "basic") {
      const cache = await caches.open(CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (offline) {
    const cached = await caches.match(request);
    if (cached) return cached;
    // A standalone window has no address bar, so the browser's own error page
    // is a dead end with no way out of it. This one at least says what
    // happened, in Hebrew, and offers to try again.
    if (request.mode === "navigate") {
      const page = await caches.match(OFFLINE_PAGE);
      if (page) return page;
    }
    throw offline;
  }
}

if (typeof self !== "undefined" && typeof self.addEventListener === "function") {
  self.addEventListener("install", (event) => {
    event.waitUntil(
      caches
        .open(CACHE)
        .then((cache) => cache.addAll(PRECACHE))
        // Take over as soon as the new worker is ready. The alternative is a
        // fix that lands for everyone except the person who already had the
        // app open, which for a one-user app is the wrong way round.
        .then(() => self.skipWaiting()),
    );
  });

  self.addEventListener("activate", (event) => {
    event.waitUntil(
      caches
        .keys()
        .then((names) =>
          Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))),
        )
        .then(() => self.clients.claim()),
    );
  });

  self.addEventListener("fetch", (event) => {
    const strategy = strategyFor(event.request, self.location.origin);
    // Not calling respondWith is not the same as passing the request through
    // a fetch here: it leaves the browser to do exactly what it would have
    // done without a worker, which is what "never cached" has to mean.
    if (strategy === "network") return;
    event.respondWith(
      strategy === "immutable" ? fromCacheFirst(event.request) : fromNetworkFirst(event.request),
    );
  });
}

// For `node --test`, which loads this file to check the routing rule. In a
// browser `module` is undefined and this is a no-op.
if (typeof module !== "undefined") {
  module.exports = { strategyFor, CACHE, PRECACHE, OFFLINE_PAGE };
}
