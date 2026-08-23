/**
 * The A–B loop (T-5.2): mark a section, sing it until it is yours.
 *
 * Chapter 8 lists this on the timeline, and it is the one control here that is
 * about practising rather than performing - the hard line in the middle of a
 * song, over and over, without hunting for it on the scrubber each time.
 *
 * The rules live apart from the component for the reason the mixer's do: they
 * are decisions with edges (an empty mark, a backwards pair, a loop too short
 * to be a phrase) and every one of them is testable in a millisecond.
 *
 * Times are **seconds of song**, the same unit the engine reports. That is what
 * makes the loop survive a tempo change without arithmetic: at 75% the read
 * head moves more slowly through the same seconds, which is exactly what
 * looping the same phrase slower means.
 */

export interface Loop {
  /** Where the section starts, in seconds. Null until it is marked. */
  a: number | null;
  /** Where it ends. Null until it is marked. */
  b: number | null;
}

export const NO_LOOP: Loop = { a: null, b: null };

/**
 * Below this the loop is a buzz rather than a phrase, and with the vocoder
 * running it is also where the artefacts live. A double-tap on the two buttons
 * is the case this exists for; two marks made while listening are never this
 * close.
 */
export const MIN_LOOP_SECONDS = 1;

/**
 * How often the loop is checked when animation frames are not running.
 *
 * A hidden tab freezes `requestAnimationFrame` - measured at zero frames in two
 * seconds, with the audio still playing - and a loop that stops repeating
 * changes what the user hears rather than what they see. Browsers clamp timers
 * in hidden tabs to roughly a second anyway, so this is a floor and not a
 * promise.
 */
export const LOOP_CHECK_MS = 200;

function clamp(seconds: number, duration: number): number {
  if (!Number.isFinite(seconds)) return 0;
  return Math.max(0, Math.min(duration, seconds));
}

/**
 * Mark the start at `at`.
 *
 * If that would land on or after the existing end, the end is dropped rather
 * than swapped: marking a new start is how someone moves the section forward,
 * and keeping a stale end behind the playhead would loop backwards over music
 * they have just left.
 */
export function markStart(loop: Loop, at: number, duration: number): Loop {
  const a = clamp(at, duration);
  const keepEnd = loop.b !== null && loop.b - a >= MIN_LOOP_SECONDS;
  return { a, b: keepEnd ? loop.b : null };
}

/**
 * Mark the end at `at`.
 *
 * With no start yet, the song so far *is* the section: a start of zero is what
 * someone means when they hit "end" first, and inventing nothing is worse than
 * assuming the obvious.
 *
 * An end before the start is not a mistake to refuse - it is somebody marking
 * the two edges in the order they heard them - so the pair is put in order.
 */
export function markEnd(loop: Loop, at: number, duration: number): Loop {
  const marked = clamp(at, duration);
  const start = loop.a ?? 0;
  const [a, b] = marked < start ? [marked, start] : [start, marked];
  if (b - a < MIN_LOOP_SECONDS) {
    // Long enough to be a phrase, and never past the end of the song.
    const end = Math.min(duration, a + MIN_LOOP_SECONDS);
    return { a: Math.max(0, end - MIN_LOOP_SECONDS), b: end };
  }
  return { a, b };
}

export function clearLoop(): Loop {
  return NO_LOOP;
}

/** A loop only exists once both ends do. One mark is a plan, not a loop. */
export function isLooping(loop: Loop): loop is { a: number; b: number } {
  return loop.a !== null && loop.b !== null && loop.b - loop.a >= MIN_LOOP_SECONDS;
}

/**
 * Whether this frame is the one that crosses the end marker.
 *
 * Deliberately a *crossing* and not "is past the end": someone who drags the
 * scrubber beyond the section has left it on purpose, and a loop that yanked
 * them back would be fighting them. Only playback that runs into the end from
 * inside wraps.
 */
export function crossedEnd(loop: Loop, previous: number, current: number): boolean {
  if (!isLooping(loop)) return false;
  return previous < loop.b && current >= loop.b;
}

/** Where a wrap lands. */
export function wrapTo(loop: Loop): number {
  return isLooping(loop) ? loop.a : 0;
}

/**
 * The section as a fraction of the song, for drawing it under the scrubber.
 * Null when there is nothing to draw.
 */
export function loopBand(loop: Loop, duration: number): { from: number; to: number } | null {
  if (!isLooping(loop) || duration <= 0) return null;
  return { from: (loop.a / duration) * 100, to: (loop.b / duration) * 100 };
}
