/**
 * Which line is being sung, and which word inside it (T-2.6, D-09).
 *
 * Apart from the component on purpose: this is the part with a number in the
 * acceptance criterion - the current line within 100ms of where it is sung -
 * and a rule that can be tested in a millisecond is a rule that stays right.
 *
 * Everything here takes a position in **milliseconds**, because that is what
 * the lyrics are stored in (T-2.1). The engine reports seconds; the conversion
 * happens once, at the edge, in the component.
 */

import type { LyricLine } from "@/lib/api";

/**
 * How long a line stays highlighted after its own end, when the next one has
 * not started yet.
 *
 * Without it, every gap between phrases blanks the screen for a moment and the
 * lyrics area flickers through an entire song. With it, the words you have just
 * sung stay up until the next ones are due, which is what a karaoke screen has
 * always done.
 */
export const HOLD_MS = 1_200;

export interface Highlight {
  /** The line being sung now, or null in a gap between lines. */
  current: number | null;
  /** The line to show dimmed underneath. Null at the end of the song. */
  next: number | null;
}

/**
 * How far the words can be nudged, and by how much at a time (T-2.7).
 *
 * Phase 0 measured the systematic bias of a whole song at +180ms, +540ms and
 * -180ms on three songs, so the useful range is small - three seconds is
 * already far past anything measured, and a wider one only makes the control
 * harder to use. The step is 100ms because that is the unit the whole feature
 * is judged in: chapter 8 asks for the current line within 100ms.
 *
 * The same measurement is also why this control cannot be the whole answer:
 * the spread *within* one song reached a p90 of 1.7s, and one number cannot
 * fix a spread. That is what T-2.9 is for.
 */
export const OFFSET_RANGE = { min: -3_000, max: 3_000 } as const;
export const OFFSET_STEP_MS = 100;

export function clampOffset(ms: number): number {
  if (!Number.isFinite(ms)) return 0;
  return Math.max(OFFSET_RANGE.min, Math.min(OFFSET_RANGE.max, Math.round(ms)));
}

/** One press of a nudge button. */
export function stepOffset(current: number, deltaMs: number): number {
  return clampOffset(current + deltaMs);
}

/**
 * The user's offset (T-2.7) applied to a stored time.
 *
 * Positive means **later**: the words are shown further into the song than they
 * are stored. That is the direction the word "offset" reads in - the same as
 * every subtitle player's delay - and the fix for lyrics that arrive early.
 */
export function shifted(ms: number | null, offsetMs: number): number | null {
  return ms === null ? null : ms + offsetMs;
}

/** The index of the last line that has started by `positionMs`, or -1. */
export function startedBy(lines: LyricLine[], positionMs: number, offsetMs = 0): number {
  let low = 0;
  let high = lines.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const start = shifted(lines[middle].start_ms, offsetMs);
    if (start !== null && start <= positionMs) {
      found = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return found;
}

/**
 * What to show at `positionMs`.
 *
 * A line stops being current when the next one starts, when its own end has
 * passed by more than `HOLD_MS`, or - for a line with no end at all, which
 * T-2.5 produces when the model's duration was not believable - when the next
 * line starts and not before.
 */
export function highlightAt(
  lines: LyricLine[],
  positionMs: number,
  offsetMs = 0,
): Highlight {
  if (lines.length === 0) return { current: null, next: null };

  const started = startedBy(lines, positionMs, offsetMs);
  if (started < 0) {
    // Before the first line: nothing is being sung, and the first line is what
    // is coming. That is the intro, and showing the words that are about to
    // start is the difference between waiting and being ready.
    return { current: null, next: 0 };
  }

  const line = lines[started];
  const end = shifted(line.end_ms, offsetMs);
  const nextStart = started + 1 < lines.length ? shifted(lines[started + 1].start_ms, offsetMs) : null;

  const overrun = end !== null && positionMs > end + HOLD_MS;
  const nextIsDue = nextStart !== null && positionMs >= nextStart;

  if (overrun && !nextIsDue) {
    return { current: null, next: started + 1 < lines.length ? started + 1 : null };
  }
  return { current: started, next: started + 1 < lines.length ? started + 1 : null };
}

/**
 * The word being sung inside a line, or null.
 *
 * Only ever asked about lines that carry word timings at all: T-2.5 keeps them
 * on a line only when the model was confident and the timings hold together,
 * because phase 0 measured a word-level highlight as a default and called it
 * broken.
 */
export function wordAt(line: LyricLine, positionMs: number, offsetMs = 0): number | null {
  if (line.words.length === 0) return null;

  let found: number | null = null;
  for (let index = 0; index < line.words.length; index += 1) {
    const start = shifted(line.words[index].start_ms, offsetMs);
    if (start === null || start > positionMs) break;
    found = index;
  }
  if (found === null) return null;

  // Past the end of the last word by a long way - the line is over and the
  // highlight should not sit on its final word for another six seconds.
  const last = line.words[found];
  const end = shifted(last.end_ms ?? last.start_ms, offsetMs);
  if (found === line.words.length - 1 && end !== null && positionMs > end + HOLD_MS) {
    return null;
  }
  return found;
}
