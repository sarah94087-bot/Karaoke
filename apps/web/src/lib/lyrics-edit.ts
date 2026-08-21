/**
 * The rules of editing the words (T-2.8).
 *
 * Apart from the screen, because two of them are decisions rather than
 * rendering, and both are easy to get quietly wrong:
 *
 * **A line's timing survives an edit of its text.** Fixing a spelling must not
 * move a line, and this is the whole reason the editor is a list of lines
 * rather than one big textarea: a textarea would make the mapping between text
 * and times a matter of counting newlines, and one stray newline would shift
 * every timing in the song by one line. (Pasting a whole set of words with no
 * times is a different job, and it is T-2.10's.)
 *
 * **A line's *word* timings do not survive it.** They are per word, and after
 * an edit they are timings for words that are no longer there - a highlight
 * that lights the wrong syllable and then runs out. So an edited line keeps its
 * text and its line-level timing, which is exactly what T-2.5 does with words
 * it does not trust, and what D-09 calls the normal case.
 */

import type { LyricLine, LyricLineIn } from "@/lib/api";

export interface EditableLine {
  /** What the line said when the editor opened. */
  original: string;
  text: string;
  start_ms: number | null;
  end_ms: number | null;
  words: LyricLine["words"];
}

export function toEditable(lines: LyricLine[]): EditableLine[] {
  return lines.map((line) => ({
    original: line.text,
    text: line.text,
    start_ms: line.start_ms,
    end_ms: line.end_ms,
    words: line.words,
  }));
}

export function editLine(lines: EditableLine[], index: number, text: string): EditableLine[] {
  if (index < 0 || index >= lines.length) return lines;
  const next = [...lines];
  next[index] = { ...next[index], text };
  return next;
}

export function isChanged(line: EditableLine): boolean {
  return line.text.trim() !== line.original.trim();
}

export function changedCount(lines: EditableLine[]): number {
  return lines.filter(isChanged).length;
}

/**
 * Whether this line still has word-level timing after the edit.
 *
 * Only shown so the screen can be honest about it before the save rather than
 * after: someone fixing one word in a line should know they are trading the
 * word highlight for a correct word, and they still will.
 */
export function keepsWords(line: EditableLine): boolean {
  return line.words.length > 0 && !isChanged(line);
}

/**
 * What gets sent.
 *
 * Blank lines are left in: the API drops them (T-2.1) and re-indexes what is
 * left, which is exactly the behaviour someone emptying a line is asking for -
 * a line the model heard and nobody sang should be removable by clearing it.
 */
export function toSave(lines: EditableLine[]): LyricLineIn[] {
  return lines.map((line) => ({
    text: line.text,
    start_ms: line.start_ms,
    end_ms: line.end_ms,
    words: keepsWords(line) ? line.words : [],
  }));
}

/** `1:04.2`, for a list where every row shows when it is sung. */
export function timecode(ms: number | null): string {
  if (ms === null) return "—";
  const total = Math.max(0, ms) / 1000;
  const minutes = Math.floor(total / 60);
  const seconds = total - minutes * 60;
  return `${minutes}:${seconds < 10 ? "0" : ""}${seconds.toFixed(1)}`;
}
