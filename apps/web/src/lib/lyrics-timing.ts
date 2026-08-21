/**
 * Moving a line in time (T-2.9).
 *
 * Phase 0 designed this task twice. `T-0.5.2` measured why it is needed: the
 * offset between the words and the singing is not constant *within* a song -
 * the spread reached a p90 of 1.7s - so T-2.7's single number cannot fix it and
 * lines have to be movable one at a time.
 *
 * `T-0.5.3` then measured how such a tool goes wrong. Tapping along in real
 * time failed the same way in two sessions on two songs: reading the words and
 * pressing in time at once is a double task, and it slid the whole take by a
 * line. Its recommendation was **a rough pass, then a correction pass where a
 * line is looped and nudged** - never precision in real time. That is the shape
 * of the screen this module serves.
 *
 * Two rules here, and both are about not breaking something else:
 *
 * **A shift takes the line's words with it.** `T-0.5.2` found the word timings
 * relatively right and absolutely wrong, so moving the whole line by the same
 * delta is exactly the correction that keeps the good half and fixes the bad
 * half. Dropping the words would throw away a measurement.
 *
 * **Starts stay in ascending order.** The player finds the current line by
 * binary search (T-2.6), which is only correct on a sorted list. A line that
 * jumps behind its predecessor would not be "early", it would be invisible.
 */

import type { EditableLine } from "@/lib/lyrics-edit";

/** One press of a nudge button. The same 100ms the offset control steps by. */
export const NUDGE_MS = 100;

/** Kept apart so a line can never land exactly on its neighbour's start. */
const GAP_MS = 1;

export function boundsFor(lines: EditableLine[], index: number): { min: number; max: number } {
  let min = 0;
  for (let before = index - 1; before >= 0; before -= 1) {
    const start = lines[before].start_ms;
    if (start !== null) {
      min = start + GAP_MS;
      break;
    }
  }

  let max = Number.POSITIVE_INFINITY;
  for (let after = index + 1; after < lines.length; after += 1) {
    const start = lines[after].start_ms;
    if (start !== null) {
      max = start - GAP_MS;
      break;
    }
  }
  return { min, max: Math.max(min, max) };
}

/**
 * Put this line's start at `ms` - what "catch the time" does.
 *
 * The line's end and its words move by the same amount, so a line that was
 * internally right stays internally right. A line with no start yet simply
 * gains one.
 */
export function setStart(lines: EditableLine[], index: number, ms: number): EditableLine[] {
  if (index < 0 || index >= lines.length) return lines;
  const line = lines[index];
  const { min, max } = boundsFor(lines, index);
  const target = Math.round(Math.max(min, Math.min(max, Math.max(0, ms))));

  const delta = line.start_ms === null ? 0 : target - line.start_ms;
  const next = [...lines];
  next[index] = {
    ...line,
    start_ms: target,
    end_ms: line.end_ms === null ? null : Math.max(target, line.end_ms + delta),
    words: line.words.map((word) => ({
      ...word,
      start_ms: word.start_ms + delta,
      end_ms: word.end_ms === null ? null : word.end_ms + delta,
    })),
  };
  return next;
}

/** One nudge, in either direction. Untimed lines have nothing to nudge. */
export function nudge(lines: EditableLine[], index: number, deltaMs: number): EditableLine[] {
  const line = lines[index];
  if (line === undefined || line.start_ms === null) return lines;
  return setStart(lines, index, line.start_ms + deltaMs);
}

/**
 * Where playback should start to hear a line from its beginning.
 *
 * A little before it, because the point of pressing play on a line is to hear
 * whether the words land with the singing - and a line that starts at the exact
 * moment you press it gives you nothing to compare against.
 */
export const LEAD_IN_MS = 1_500;

export function playFrom(line: EditableLine): number {
  return Math.max(0, (line.start_ms ?? 0) - LEAD_IN_MS);
}

/**
 * Where a looped line should end.
 *
 * The line's own end when it has one; otherwise the next line's start, and
 * failing that a few seconds - long enough to hear the phrase, short enough to
 * come round again while it is still in your head.
 */
export const LOOP_FALLBACK_MS = 6_000;

export function loopEnd(lines: EditableLine[], index: number): number {
  const line = lines[index];
  if (line === undefined) return 0;
  const start = line.start_ms ?? 0;
  if (line.end_ms !== null) return Math.max(start + 500, line.end_ms);
  for (let after = index + 1; after < lines.length; after += 1) {
    const next = lines[after].start_ms;
    if (next !== null) return Math.max(start + 500, next);
  }
  return start + LOOP_FALLBACK_MS;
}
