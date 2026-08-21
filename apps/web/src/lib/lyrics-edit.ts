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
  /** And when it started. T-2.9 moves lines, and "changed" has to include that. */
  originalStart: number | null;
  text: string;
  start_ms: number | null;
  end_ms: number | null;
  words: LyricLine["words"];
}

export function toEditable(lines: LyricLine[]): EditableLine[] {
  return lines.map((line) => ({
    original: line.text,
    originalStart: line.start_ms,
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

/** Moved in time (T-2.9), which is a change to save even if the text is the same. */
export function isMoved(line: EditableLine): boolean {
  return line.start_ms !== line.originalStart;
}

export function changedCount(lines: EditableLine[]): number {
  return lines.filter((line) => isChanged(line) || isMoved(line)).length;
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

/**
 * Words somebody already has, turned into lines (T-2.10).
 *
 * D-08's three sources are the open database, the transcription, and the
 * editor - and this is the editor's own source. Someone who has the words
 * already, from a lyrics site or from memory, should not have to wait for a
 * model to guess them and then correct the guess.
 *
 * The lines come out **untimed on purpose**. T-2.1 stores `start_ms: null`
 * happily and reports the song as `missing` until times exist, which is the
 * truth: the words are right and the timing has not been done. The timing is
 * then the same job T-2.9 already does, one line at a time.
 */
export function fromPaste(text: string): EditableLine[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => ({
      original: "",
      originalStart: null,
      text: line,
      start_ms: null,
      end_ms: null,
      words: [],
    }));
}

/**
 * The next line still waiting for a time, for the rough pass.
 *
 * Phase 0's warning about tapping along (T-0.5.3) was that hunting for the
 * right control while reading and listening is what slides a whole take by a
 * line. One button that always means "the next one" removes the hunting; the
 * correction pass then fixes what the tapping got wrong.
 */
export function nextUntimed(lines: EditableLine[], after = -1): number | null {
  for (let index = after + 1; index < lines.length; index += 1) {
    if (lines[index].start_ms === null) return index;
  }
  for (let index = 0; index <= after && index < lines.length; index += 1) {
    if (lines[index].start_ms === null) return index;
  }
  return null;
}
