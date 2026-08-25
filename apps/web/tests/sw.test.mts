/**
 * The service worker's routing rule (T-5.3).
 *
 * `public/sw.js` cannot be imported by the app - it is registered by URL and
 * never bundled, the same constraint `pitch-worklet.js` has - so it is loaded
 * here the way the browser would not: as a CommonJS module, which is what the
 * `typeof module` guard at the bottom of that file is for. The listeners are
 * behind a `typeof self` guard and do not run.
 *
 * Only the rule is tested, and that is the point: every branch of it is a
 * decision that fails *silently* if it is wrong. A cached API response is a
 * library that never updates; a cached signed link is megabytes held under a
 * URL that stops working in an hour; a cached page after a deploy is a fix
 * that never arrives.
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { strategyFor, PRECACHE, OFFLINE_PAGE } = require("../public/sw.js");

const SELF = "https://karaoke-theta-blue.vercel.app";
const get = (url: string) => ({ method: "GET", url });

test("anything that is not a GET goes to the network", () => {
  // Uploading a song, saving settings, marking a song played.
  assert.equal(strategyFor({ method: "POST", url: `${SELF}/he` }, SELF), "network");
  assert.equal(strategyFor({ method: "PATCH", url: `${SELF}/he` }, SELF), "network");
});

test("the API is never cached", () => {
  // A cached 401 from an expired session would lock somebody out of their own
  // songs until they cleared storage.
  assert.equal(
    strategyFor(get("https://karuki-api.onrender.com/api/v1/songs"), SELF),
    "network",
  );
});

test("the bucket is never cached", () => {
  // Signed, expiring, and megabytes each.
  assert.equal(
    strategyFor(
      get("https://s3.eu-central-003.backblazeb2.com/karuki-songs-sarah/songs/x/stems/vocals.mp3"),
      SELF,
    ),
    "network",
  );
});

test("nobody else's origin is ours to hold", () => {
  assert.equal(strategyFor(get("https://ckzfdxdgzkkedpycjsiw.supabase.co/auth/v1/user"), SELF), "network");
  assert.equal(strategyFor(get("https://o4511966632738816.ingest.de.sentry.io/api/x/envelope/"), SELF), "network");
});

test("a signed link on our own origin is refused too", () => {
  // Chapter 11 keeps the whole product runnable on one machine, where the API
  // shares this origin and serves stems from `/api/v1/files/...` (T-3.1).
  assert.equal(strategyFor(get(`${SELF}/api/v1/files/songs/x/stems/bass.mp3?expires=1&sig=ab`), SELF), "network");
  assert.equal(strategyFor(get(`${SELF}/files/x.mp3?X-Amz-Signature=deadbeef`), SELF), "network");
});

test("content-hashed build output is cached forever", () => {
  // This is where "opens fast" comes from: the file name changes when the file
  // does, so there is no such thing as a stale answer here.
  assert.equal(strategyFor(get(`${SELF}/_next/static/chunks/main-a1b2c3.js`), SELF), "immutable");
  assert.equal(strategyFor(get(`${SELF}/_next/static/css/app-d4e5.css`), SELF), "immutable");
});

test("our own files that are not hashed are fetched fresh first", () => {
  // The worklet is the one that matters: T-1.12's drift measurements belong to
  // a specific version of it, and it is served under a name that never changes.
  assert.equal(strategyFor(get(`${SELF}/pitch-worklet.js`), SELF), "fresh");
  assert.equal(strategyFor(get(`${SELF}/icon-512.png`), SELF), "fresh");
  assert.equal(strategyFor(get(`${SELF}/manifest.webmanifest`), SELF), "fresh");
});

test("pages are fetched fresh first, so a deploy is picked up at once", () => {
  assert.equal(strategyFor(get(`${SELF}/he`), SELF), "fresh");
  assert.equal(strategyFor(get(`${SELF}/he/songs/abc`), SELF), "fresh");
});

test("the offline page is precached, or there is nothing to fall back to", () => {
  assert.ok(PRECACHE.includes(OFFLINE_PAGE));
});
