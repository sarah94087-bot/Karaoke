/**
 * The playback queue (T-5.1).
 *
 * The rules worth pinning are the ones that decide what happens *between*
 * songs, which is the moment nobody is watching the screen: what follows this
 * song, what follows the last one, and what follows a song that was never in
 * the queue at all. Getting the last one wrong would drop somebody who opened
 * one song from the library into an evening they did not ask for.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  EMPTY_QUEUE,
  MAX_QUEUE,
  QUEUE_STORAGE_KEY,
  contains,
  dequeue,
  enqueue,
  head,
  loadQueue,
  nextAfter,
  parseQueue,
  positionOf,
  refresh,
} from "../src/lib/player/queue.ts";

const a = { id: "a", title: "עוף גוזל" };
const b = { id: "b", title: "ממעמקים" };
const c = { id: "c", title: "מחכים למשיח" };

function evening() {
  return enqueue(enqueue(enqueue(EMPTY_QUEUE, a), b), c);
}

test("songs queue in the order they were added", () => {
  assert.deepEqual(
    evening().entries.map((entry) => entry.id),
    ["a", "b", "c"],
  );
});

test("adding a song already queued changes nothing", () => {
  const queue = evening();
  const again = enqueue(queue, { id: "a", title: "עוף גוזל" });
  // Not moved to the end either: pressing the button twice must not silently
  // reorder an evening somebody has already arranged.
  assert.deepEqual(again.entries.map((entry) => entry.id), ["a", "b", "c"]);
});

test("the next song is the one after this one", () => {
  assert.equal(nextAfter(evening(), "a")?.id, "b");
  assert.equal(nextAfter(evening(), "b")?.id, "c");
});

test("the last song ends the evening", () => {
  assert.equal(nextAfter(evening(), "c"), null);
});

test("a song that is not in the queue leads nowhere", () => {
  // Opening one song from the library mid-evening must not join the running
  // order at whatever position happens to match.
  assert.equal(nextAfter(evening(), "somewhere-else"), null);
});

test("removing a song closes the gap", () => {
  const queue = dequeue(evening(), "b");
  assert.equal(nextAfter(queue, "a")?.id, "c");
  assert.equal(contains(queue, "b"), false);
});

test("positions are 1-based, and zero means not queued", () => {
  assert.equal(positionOf(evening(), "b"), 2);
  assert.equal(positionOf(evening(), "zzz"), 0);
});

test("the evening starts at the head", () => {
  assert.equal(head(evening())?.id, "a");
  assert.equal(head(EMPTY_QUEUE), null);
});

test("a renamed song is corrected in place", () => {
  // T-4.2 lets a title change after the queue was built. The stored one is a
  // snapshot, and this is what keeps the snapshot honest.
  const queue = refresh(evening(), { id: "b", title: "ממעמקים (עידן רייכל)" });
  assert.equal(queue.entries[1].title, "ממעמקים (עידן רייכל)");
  assert.equal(queue.entries[1].id, "b");
  assert.equal(queue.entries.length, 3);
});

test("a title that has not changed returns the same object", () => {
  const queue = evening();
  assert.equal(refresh(queue, a), queue);
});

test("the queue cannot grow without bound", () => {
  let queue = EMPTY_QUEUE;
  for (let i = 0; i < MAX_QUEUE + 10; i++) {
    queue = enqueue(queue, { id: `song-${i}`, title: `${i}` });
  }
  assert.equal(queue.entries.length, MAX_QUEUE);
});

test("rubbish in storage reads as an empty queue", () => {
  // The key is in a place the user can edit and it survives a deploy that
  // changes the shape. Nothing here is worth an exception on a screen that is
  // about to play music.
  assert.deepEqual(parseQueue("not json"), EMPTY_QUEUE);
  assert.deepEqual(parseQueue("[]"), EMPTY_QUEUE);
  assert.deepEqual(parseQueue('{"entries": "no"}'), EMPTY_QUEUE);
  assert.deepEqual(parseQueue(null), EMPTY_QUEUE);
});

test("entries without an id are dropped, and duplicates with them", () => {
  const queue = parseQueue(
    JSON.stringify({ entries: [{ title: "no id" }, a, { id: "a", title: "again" }, b] }),
  );
  assert.deepEqual(queue.entries.map((entry) => entry.id), ["a", "b"]);
});

test("a queue survives a round trip through storage", () => {
  const store = new Map<string, string>();
  const fake = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
  } as unknown as Storage;

  store.set(QUEUE_STORAGE_KEY, JSON.stringify(evening()));
  assert.deepEqual(loadQueue(fake).entries.map((entry) => entry.id), ["a", "b", "c"]);
});

test("storage that throws is not a reason to fail", () => {
  const broken = {
    getItem: () => {
      throw new Error("private browsing");
    },
  } as unknown as Storage;
  assert.deepEqual(loadQueue(broken), EMPTY_QUEUE);
});
