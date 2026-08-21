/**
 * The two decisions inside editing the words (T-2.8).
 *
 * Phase 0's `T-0.4.3` is why the editor shows every line at once - 64 words
 * were corrected against 32 flagged as low confidence - but that is a shape,
 * not a rule to test. These are the rules: a line's *timing* survives an edit
 * of its text, and its *word* timings do not.
 */

import assert from "node:assert/strict";
import test from "node:test";

import type { LyricLine } from "../src/lib/api.ts";
import {
  changedCount,
  editLine,
  isChanged,
  keepsWords,
  timecode,
  toEditable,
  toSave,
} from "../src/lib/lyrics-edit.ts";

function line(text: string, start: number | null, words: LyricLine["words"] = []): LyricLine {
  return { index: 0, text, start_ms: start, end_ms: start === null ? null : start + 3_000, words };
}

const WORDS = [
  { text: "שתי", start_ms: 1_000, end_ms: 1_500 },
  { text: "מילים", start_ms: 1_500, end_ms: 2_000 },
];

test("fixing a spelling does not move the line", () => {
  const lines = editLine(toEditable([line("שורה שגויה", 4_000)]), 0, "שורה מתוקנת");

  const [saved] = toSave(lines);
  assert.equal(saved.text, "שורה מתוקנת");
  assert.equal(saved.start_ms, 4_000);
  assert.equal(saved.end_ms, 7_000);
});

test("an edited line gives up its word timings", () => {
  // They are timings for words that are no longer there: a highlight that
  // lights the wrong syllable and then runs out is worse than none, and
  // line-level is what D-09 calls the normal case anyway.
  const lines = editLine(toEditable([line("שתי מילים", 1_000, WORDS)]), 0, "שתי מילים נוספות");

  assert.equal(toSave(lines)[0].words.length, 0);
});

test("a line nobody touched keeps them", () => {
  const lines = toEditable([line("שתי מילים", 1_000, WORDS)]);

  assert.equal(toSave(lines)[0].words.length, 2);
  assert.equal(keepsWords(lines[0]), true);
});

test("the screen can say so before the save rather than after", () => {
  const edited = editLine(toEditable([line("שתי מילים", 1_000, WORDS)]), 0, "שלוש מילים");

  assert.equal(keepsWords(edited[0]), false);
});

test("whitespace alone is not an edit", () => {
  // Otherwise every line someone tabs through comes back as "changed" and the
  // save button lies about how much work was done.
  const lines = editLine(toEditable([line("שורה", 1_000, WORDS)]), 0, "  שורה  ");

  assert.equal(isChanged(lines[0]), false);
  assert.equal(toSave(lines)[0].words.length, 2);
});

test("the changed count is what the save button reports", () => {
  let lines = toEditable([line("א", 0), line("ב", 1_000), line("ג", 2_000)]);
  lines = editLine(lines, 0, "אחת");
  lines = editLine(lines, 2, "שלוש");

  assert.equal(changedCount(lines), 2);
});

test("emptying a line is how a line gets deleted", () => {
  // The API drops blank lines and re-indexes the rest (T-2.1), so clearing one
  // the model heard but nobody sang does exactly what it looks like.
  const lines = editLine(toEditable([line("שורה מומצאת", 1_000)]), 0, "");

  assert.equal(toSave(lines)[0].text, "");
});

test("an untimed line stays untimed", () => {
  const lines = editLine(toEditable([line("שורה בלי זמן", null)]), 0, "שורה מתוקנת");

  assert.equal(toSave(lines)[0].start_ms, null);
});

test("editing outside the list changes nothing", () => {
  const lines = toEditable([line("שורה", 0)]);

  assert.deepEqual(editLine(lines, 5, "אחר"), lines);
});

test("the timecode is readable at a glance", () => {
  assert.equal(timecode(0), "0:00.0");
  assert.equal(timecode(64_200), "1:04.2");
  assert.equal(timecode(null), "—");
});
