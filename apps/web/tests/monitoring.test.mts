/**
 * The envelope, which is all that hand-writing this costs (T-3.12).
 *
 * Both functions are pure so they can be checked here rather than by staring
 * at a dashboard: a report that is silently malformed is indistinguishable
 * from an app that has no errors.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { buildEnvelope, parseDsn } from "../src/lib/monitoring.ts";

const DSN = "https://abc123@o4509.ingest.de.sentry.io/4511966642176080";

test("the ingest URL and key come out of the DSN", () => {
  const target = parseDsn(DSN);

  assert.equal(target?.url, "https://o4509.ingest.de.sentry.io/api/4511966642176080/envelope/");
  assert.equal(target?.key, "abc123");
});

test("a DSN that is not one is not a crash", () => {
  // The page must keep working when the variable is empty, half-pasted, or
  // something else entirely.
  assert.equal(parseDsn(""), null);
  assert.equal(parseDsn("not a url"), null);
  assert.equal(parseDsn("https://o4509.ingest.de.sentry.io/4511"), null, "no key");
  assert.equal(parseDsn("https://abc123@o4509.ingest.de.sentry.io/"), null, "no project");
});

test("the envelope is three lines: headers, item, event", () => {
  const body = buildEnvelope(
    { name: "TypeError", message: "x is not a function", stack: "at play (player.ts:12)" },
    {
      url: "https://karaoke.example/he/songs/1",
      id: "0123456789abcdef0123456789abcdef",
      sentAt: "2026-08-24T18:00:00.000Z",
      environment: "production",
    },
  );
  const [headers, item, event] = body.split("\n").map((line) => JSON.parse(line));

  assert.equal(headers.event_id, "0123456789abcdef0123456789abcdef");
  assert.equal(item.type, "event");
  assert.equal(event.exception.values[0].type, "TypeError");
  assert.equal(event.exception.values[0].value, "x is not a function");
  assert.equal(event.extra.stack, "at play (player.ts:12)");
  assert.equal(event.request.url, "https://karaoke.example/he/songs/1");
  assert.equal(event.environment, "production");
  // Seconds, not milliseconds - Sentry reads this as a unix timestamp and a
  // report a thousand times in the future is one nobody will ever see.
  assert.equal(event.timestamp, 1787594400);
});

test("a release is included only when there is one", () => {
  const without = JSON.parse(
    buildEnvelope(
      { message: "boom" },
      { url: "u", id: "i", sentAt: "2026-08-24T18:00:00.000Z", environment: "local" },
    ).split("\n")[2],
  );

  assert.equal("release" in without, false);

  const withRelease = JSON.parse(
    buildEnvelope(
      { message: "boom" },
      {
        url: "u",
        id: "i",
        sentAt: "2026-08-24T18:00:00.000Z",
        environment: "local",
        release: "0.1.0",
      },
    ).split("\n")[2],
  );

  assert.equal(withRelease.release, "0.1.0");
});

test("an error with no stack still reports", () => {
  // `throw "something"` and cross-origin script errors both arrive this way,
  // and they are exactly the ones worth hearing about.
  const event = JSON.parse(
    buildEnvelope(
      { message: "Script error." },
      { url: "u", id: "i", sentAt: "2026-08-24T18:00:00.000Z", environment: "production" },
    ).split("\n")[2],
  );

  assert.equal(event.exception.values[0].type, "Error");
  assert.equal(event.extra.stack, "(no stack)");
});
