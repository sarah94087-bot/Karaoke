/**
 * How key and tempo are shown and stepped.
 *
 * T-1.14's acceptance criterion has two halves - the change is immediate, and
 * **the value is shown to the user** - and this file is the second half. A
 * slider with no number on it is the version of this feature that looks
 * finished and is not: "somewhere around plus two" is not a key you can come
 * back to tomorrow.
 *
 * Kept out of the component so the formatting can be tested, because the
 * interesting cases are all edges: zero, the ends of the range, and a fraction
 * arriving from a range input.
 */

// The extension is explicit because Node runs these modules directly when
// `node --test` strips their types, and its ESM resolver does not guess.
// `allowImportingTsExtensions` in tsconfig is what lets the bundler agree.
import { KEY_RANGE, TEMPO_RANGE } from "./engine.ts";

/** Every key a user can choose: thirteen, chapter 8's -6..+6. */
export const KEY_STEPS: readonly number[] = Array.from(
  { length: KEY_RANGE.max - KEY_RANGE.min + 1 },
  (_, index) => KEY_RANGE.min + index,
);

/** Chapter 8's 50%-150%, in steps a user can actually aim at. */
export const TEMPO_STEP = 0.05;

export const DEFAULT_KEY = 0;
export const DEFAULT_TEMPO = 1;

export function clampKey(semitones: number): number {
  if (Number.isNaN(semitones)) return DEFAULT_KEY;
  return Math.max(KEY_RANGE.min, Math.min(KEY_RANGE.max, Math.round(semitones)));
}

export function clampTempo(ratio: number): number {
  if (Number.isNaN(ratio)) return DEFAULT_TEMPO;
  const stepped = Math.round(ratio / TEMPO_STEP) * TEMPO_STEP;
  return Math.max(TEMPO_RANGE.min, Math.min(TEMPO_RANGE.max, Number(stepped.toFixed(2))));
}

export function stepKey(semitones: number, delta: number): number {
  return clampKey(semitones + delta);
}

/**
 * The sign is explicit for anything other than zero: "+2" reads as a change,
 * "2" reads as a setting whose meaning you have to remember. A true minus sign
 * rather than a hyphen, which is what the spec itself writes.
 */
export function formatKey(semitones: number): string {
  const value = clampKey(semitones);
  if (value === 0) return "0";
  return value > 0 ? `+${value}` : `−${Math.abs(value)}`;
}

export function formatTempo(ratio: number): string {
  return `${Math.round(clampTempo(ratio) * 100)}%`;
}

export function isDefaultKey(semitones: number): boolean {
  return clampKey(semitones) === DEFAULT_KEY;
}

export function isDefaultTempo(ratio: number): boolean {
  return clampTempo(ratio) === DEFAULT_TEMPO;
}

export function canRaiseKey(semitones: number): boolean {
  return clampKey(semitones) < KEY_RANGE.max;
}

export function canLowerKey(semitones: number): boolean {
  return clampKey(semitones) > KEY_RANGE.min;
}
