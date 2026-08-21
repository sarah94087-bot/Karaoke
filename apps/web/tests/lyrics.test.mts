/**
 * Which line is being sung (T-2.6).
 *
 * The acceptance criterion has a number in it - the current line highlighted
 * within 100ms of where it is sung - and this is the half of that number the
 * code controls. The other half is the clock, which is the engine's, and the
 * data, which is T-2.5's.
 */

import assert from "node:assert/strict";
import test from "node:test";

import type { LyricLine } from "../src/lib/api.ts";
import {
  HOLD_MS,
  OFFSET_RANGE,
  OFFSET_STEP_MS,
  clampOffset,
  highlightAt,
  startedBy,
  stepOffset,
  wordAt,
} from "../src/lib/lyrics.ts";

function line(
  index: number,
  start: number | null,
  end: number | null,
  text = `שורה ${index}`,
  words: { text: string; start_ms: number; end_ms: number | null }[] = [],
): LyricLine {
  return { index, text, start_ms: start, end_ms: end, words };
}

const VERSE = [
  line(0, 1_000, 4_000),
  line(1, 5_000, 8_000),
  line(2, 9_000, 12_000),
];

test("before the first line, the first line is what is coming", () => {
  assert.deepEqual(highlightAt(VERSE, 0), { current: null, next: 0 });
});

test("the line being sung is the current one", () => {
  assert.deepEqual(highlightAt(VERSE, 2_000), { current: 0, next: 1 });
  assert.deepEqual(highlightAt(VERSE, 6_500), { current: 1, next: 2 });
});

test("a line is current from its first millisecond", () => {
  // The whole budget is 100ms, so being one frame late at the boundary is a
  // tenth of it spent on nothing.
  assert.equal(highlightAt(VERSE, 5_000).current, 1);
  assert.equal(highlightAt(VERSE, 4_999).current, 0);
});

test("the next line takes over exactly when it starts", () => {
  assert.equal(highlightAt(VERSE, 8_999).current, 1);
  assert.equal(highlightAt(VERSE, 9_000).current, 2);
});

test("a line stays up through a short gap after it", () => {
  // Blanking the screen between phrases makes the lyrics area flicker through
  // a whole song.
  assert.equal(highlightAt(VERSE, 4_500).current, 0);
});

test("a long instrumental clears the line and shows what is coming", () => {
  const sparse = [line(0, 1_000, 4_000), line(1, 60_000, 63_000)];

  assert.deepEqual(highlightAt(sparse, 30_000), { current: null, next: 1 });
});

test("a line with no end holds until the next one starts", () => {
  // T-2.5 leaves `end_ms` null rather than believing a 15s word. The line is
  // shown until something replaces it, which is true where a guess would not
  // be.
  const open = [line(0, 1_000, null), line(1, 40_000, 43_000)];

  assert.equal(highlightAt(open, 30_000).current, 0);
  assert.equal(highlightAt(open, 41_000).current, 1);
});

test("the last line is current with nothing after it", () => {
  assert.deepEqual(highlightAt(VERSE, 10_000), { current: 2, next: null });
});

test("no lines is not an error", () => {
  assert.deepEqual(highlightAt([], 5_000), { current: null, next: null });
});

test("an untimed line is never current", () => {
  // A paste with no timings (T-2.10) is shown in the editor, not scrolled.
  assert.equal(startedBy([line(0, null, null)], 10_000), -1);
});

test("a positive offset shows the words later", () => {
  // The direction the word "offset" reads in, and the same as a subtitle
  // delay: +300ms means every line comes up 300ms further into the song.
  assert.equal(highlightAt(VERSE, 5_100).current, 1);
  assert.equal(highlightAt(VERSE, 5_100, 300).current, 0);
});

test("a negative offset shows them earlier", () => {
  assert.equal(highlightAt(VERSE, 4_800).current, 0);
  assert.equal(highlightAt(VERSE, 4_800, -300).current, 1);
});

const SUNG = line(0, 1_000, 4_000, "שתי מילים כאן", [
  { text: "שתי", start_ms: 1_000, end_ms: 2_000 },
  { text: "מילים", start_ms: 2_000, end_ms: 3_000 },
  { text: "כאן", start_ms: 3_000, end_ms: 4_000 },
]);

test("the word being sung is the highlighted one", () => {
  assert.equal(wordAt(SUNG, 1_500), 0);
  assert.equal(wordAt(SUNG, 2_500), 1);
  assert.equal(wordAt(SUNG, 3_500), 2);
});

test("no word is highlighted before the first one", () => {
  assert.equal(wordAt(SUNG, 500), null);
});

test("the last word does not stay lit forever", () => {
  assert.equal(wordAt(SUNG, 4_000 + HOLD_MS + 1), null);
});

test("a line with no word timings highlights no word", () => {
  // D-09 and T-2.5: word timings are kept only where they hold up, and the
  // rest of the song is line-level. That is a normal outcome, not a failure.
  assert.equal(wordAt(VERSE[0], 2_000), null);
});

test("the offset applies to words too", () => {
  assert.equal(wordAt(SUNG, 2_100), 1);
  assert.equal(wordAt(SUNG, 2_100, 200), 0);
});

test("a nudge moves by one step", () => {
  assert.equal(stepOffset(0, OFFSET_STEP_MS), 100);
  assert.equal(stepOffset(100, -OFFSET_STEP_MS), 0);
});

test("the nudge stops at the ends of the range", () => {
  // Phase 0 measured whole-song biases of +180ms to +540ms; three seconds is
  // already far past anything real, and a wider range only makes the control
  // harder to use.
  assert.equal(stepOffset(OFFSET_RANGE.max, OFFSET_STEP_MS), OFFSET_RANGE.max);
  assert.equal(stepOffset(OFFSET_RANGE.min, -OFFSET_STEP_MS), OFFSET_RANGE.min);
});

test("a stored offset from outside the range is brought back inside it", () => {
  // A hand-edited row, or a wider range in some future version.
  assert.equal(clampOffset(99_000), OFFSET_RANGE.max);
  assert.equal(clampOffset(Number.NaN), 0);
});
