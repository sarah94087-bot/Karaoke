/**
 * Moving a line in time (T-2.9).
 *
 * The two rules worth testing are both about not breaking something else: a
 * shift keeps the line's words with it, and starts stay in ascending order
 * because the player finds the current line by binary search.
 */

import assert from "node:assert/strict";
import test from "node:test";

import type { LyricLine } from "../src/lib/api.ts";
import { toEditable } from "../src/lib/lyrics-edit.ts";
import { NUDGE_MS, boundsFor, loopEnd, nudge, playFrom, setStart } from "../src/lib/lyrics-timing.ts";

function line(start: number | null, end: number | null = null, words: LyricLine["words"] = []): LyricLine {
  return { index: 0, text: "שורה", start_ms: start, end_ms: end, words };
}

const VERSE = toEditable([line(1_000, 4_000), line(5_000, 8_000), line(9_000, 12_000)]);

test("catching a time puts the line there", () => {
  assert.equal(setStart(VERSE, 1, 6_400)[1].start_ms, 6_400);
});

test("the line's end moves with its start", () => {
  // The line did not get longer, it happened later.
  const moved = setStart(VERSE, 1, 6_000)[1];

  assert.equal(moved.end_ms, 9_000);
});

test("a shift takes the words with it", () => {
  // T-0.5.2 found the word timings relatively right and absolutely wrong, so
  // shifting the whole line by one delta is the correction that keeps the half
  // that was measured.
  const withWords = toEditable([
    line(1_000, 4_000, [
      { text: "אחת", start_ms: 1_000, end_ms: 2_000 },
      { text: "שתיים", start_ms: 2_000, end_ms: 4_000 },
    ]),
  ]);

  const moved = setStart(withWords, 0, 1_300)[0];

  assert.deepEqual(
    moved.words.map((word) => [word.start_ms, word.end_ms]),
    [
      [1_300, 2_300],
      [2_300, 4_300],
    ],
  );
});

test("a line cannot be pushed behind the one before it", () => {
  // The player binary-searches for the current line, which is only correct on a
  // sorted list: a line that jumped backwards would not be early, it would be
  // invisible.
  const moved = setStart(VERSE, 1, 200)[1];

  assert.ok(moved.start_ms !== null && moved.start_ms > VERSE[0].start_ms!);
});

test("nor past the one after it", () => {
  const moved = setStart(VERSE, 1, 99_000)[1];

  assert.ok(moved.start_ms !== null && moved.start_ms < VERSE[2].start_ms!);
});

test("the neighbours that count are the timed ones", () => {
  const sparse = toEditable([line(2_000), line(null), line(8_000)]);

  assert.deepEqual(boundsFor(sparse, 1), { min: 2_001, max: 7_999 });
});

test("a nudge is one step in either direction", () => {
  assert.equal(nudge(VERSE, 1, NUDGE_MS)[1].start_ms, 5_100);
  assert.equal(nudge(VERSE, 1, -NUDGE_MS)[1].start_ms, 4_900);
});

test("an untimed line has nothing to nudge", () => {
  const untimed = toEditable([line(null)]);

  assert.equal(nudge(untimed, 0, NUDGE_MS)[0].start_ms, null);
});

test("catching a time on an untimed line gives it one", () => {
  const untimed = toEditable([line(null)]);

  assert.equal(setStart(untimed, 0, 4_200)[0].start_ms, 4_200);
});

test("playing a line starts a little before it", () => {
  // Pressing play on a line is a question - does this land with the singing? -
  // and starting exactly on it gives nothing to compare against.
  assert.ok(playFrom(VERSE[1]) < 5_000);
  assert.equal(playFrom(toEditable([line(200)])[0]), 0);
});

test("a looped line ends at its own end", () => {
  assert.equal(loopEnd(VERSE, 1), 8_000);
});

test("a line with no end loops until the next line starts", () => {
  const open = toEditable([line(1_000), line(6_000)]);

  assert.equal(loopEnd(open, 0), 6_000);
});

test("the last line with no end still loops", () => {
  const open = toEditable([line(1_000)]);

  assert.ok(loopEnd(open, 0) > 1_000);
});
