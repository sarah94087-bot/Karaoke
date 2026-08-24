/**
 * Framing, which is the whole of what reading SSE by hand costs.
 *
 * The parser is a pure function precisely so this can be checked in a
 * millisecond: the thing it replaces - the browser's own EventSource - was
 * never wrong about framing and was refused at the door instead, because it
 * cannot send an Authorization header (T-3.11).
 */

import assert from "node:assert/strict";
import test from "node:test";

import { parseFrames } from "../src/lib/sse.ts";

test("a whole message is read, and its name comes from the event field", () => {
  const { messages, rest } = parseFrames('event: progress\ndata: {"progress":50}\n\n');

  assert.deepEqual(messages, [{ event: "progress", data: '{"progress":50}' }]);
  assert.equal(rest, "");
});

test("half a message is kept for the next chunk", () => {
  const first = parseFrames('event: ready\ndata: {"sta');

  assert.deepEqual(first.messages, []);

  const second = parseFrames(first.rest + 'te":"ready"}\n\n');

  assert.deepEqual(second.messages, [{ event: "ready", data: '{"state":"ready"}' }]);
});

test("several messages in one chunk all arrive", () => {
  const { messages } = parseFrames(
    "event: snapshot\ndata: 1\n\nevent: progress\ndata: 2\n\nevent: ready\ndata: 3\n\n",
  );

  assert.deepEqual(
    messages.map((m) => m.event),
    ["snapshot", "progress", "ready"],
  );
});

test("the heartbeat is invisible", () => {
  // Separation sends nothing for a minute or more, so these are the only
  // traffic on the connection for most of a job (T-1.8).
  const { messages } = parseFrames(": ping\n\n: ping\n\nevent: progress\ndata: 1\n\n");

  assert.deepEqual(messages, [{ event: "progress", data: "1" }]);
});

test("the opening retry frame is not a message", () => {
  // The server sends `retry:` for EventSource's benefit. This reader does not
  // reconnect - the caller falls back to polling - so it must not be mistaken
  // for an event.
  const { messages } = parseFrames("retry: 3000\n\n");

  assert.deepEqual(messages, []);
});

test("one space after the colon belongs to the format", () => {
  const { messages } = parseFrames("event:progress\ndata:  two spaces\n\n");

  assert.deepEqual(messages, [{ event: "progress", data: " two spaces" }]);
});

test("carriage returns are the same stream", () => {
  const { messages } = parseFrames("event: ready\r\ndata: 1\r\n\r\n");

  assert.deepEqual(messages, [{ event: "ready", data: "1" }]);
});

test("a message with no data is not delivered", () => {
  const { messages } = parseFrames("event: progress\n\n");

  assert.deepEqual(messages, []);
});
