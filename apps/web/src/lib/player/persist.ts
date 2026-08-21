/**
 * Turning what the player is doing into what gets stored, and back.
 *
 * Chapter 5 says the settings are "saved automatically on every change in the
 * player", which cannot be taken literally: dragging one fader fires an event
 * per pixel, and a request per pixel would be absurd. So changes are coalesced
 * and written after a short quiet period - the user's *intent* is saved on every
 * change, which is what the sentence means.
 *
 * Kept out of the component so both halves can be tested: the debounce, and the
 * translation between the engine's shape and the API's.
 */

import type { PlayerSettings } from "@/lib/api";
// A relative specifier with its extension, not the `@/` alias: this module is
// loaded directly by `node --test`, which strips types but does not resolve
// tsconfig paths. Type-only imports are erased and can keep the alias, which is
// why the line above still does. (Same trap as T-1.14's controls.ts.)
import { clampOffset } from "../lyrics.ts";
import { KEY_RANGE, TEMPO_RANGE, type StemKind } from "./engine.ts";
import { DEFAULT_MIX, type MixState, STEM_ORDER } from "./mix.ts";

/**
 * Long enough that a fader drag is one request rather than fifty, short enough
 * that closing the tab straight after a change does not lose it.
 */
export const SAVE_DEBOUNCE_MS = 600;

export const DEFAULT_SETTINGS: PlayerSettings = {
  key_shift: 0,
  tempo_ratio: 1,
  stem_volumes: null,
  lyric_offset_ms: 0,
};

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

/**
 * What the player is currently doing, in the shape the API stores.
 *
 * The offset is a parameter rather than a constant, which it was until T-2.7 -
 * and while nothing could set one that was harmless, the moment T-2.7 added the
 * control it would have meant every fader drag quietly resetting it to zero.
 */
export function toSettings(
  mix: MixState,
  semitones: number,
  tempo: number,
  lyricOffsetMs = 0,
): PlayerSettings {
  const volumes: Record<string, number> = {};
  for (const kind of STEM_ORDER) volumes[kind] = mix.volumes[kind];

  return {
    key_shift: Math.round(clamp(semitones, KEY_RANGE.min, KEY_RANGE.max)),
    tempo_ratio: clamp(tempo, TEMPO_RANGE.min, TEMPO_RANGE.max),
    stem_volumes: volumes,
    lyric_offset_ms: clampOffset(lyricOffsetMs),
  };
}

/**
 * A stored settings row as a mix.
 *
 * `vocalsRemoved` is derived rather than stored: a vocals volume of zero *is*
 * the vocals being removed, and storing the flag separately would let the two
 * disagree after a hand-edited row.
 */
export function toMix(settings: PlayerSettings | null | undefined): MixState {
  const stored = settings?.stem_volumes;
  if (!stored) return DEFAULT_MIX;

  const volumes = { ...DEFAULT_MIX.volumes };
  for (const kind of STEM_ORDER) {
    const value = stored[kind];
    if (typeof value === "number") volumes[kind as StemKind] = clamp(value, 0, 1);
  }

  return {
    volumes,
    vocalsRemoved: volumes.vocals === 0,
    // Somewhere to go back to when the button is pressed again. A stored zero
    // would make the button a no-op.
    restoreVocalsTo: volumes.vocals > 0 ? volumes.vocals : 1,
  };
}

export function keyOf(settings: PlayerSettings | null | undefined): number {
  return Math.round(clamp(settings?.key_shift ?? 0, KEY_RANGE.min, KEY_RANGE.max));
}

export function tempoOf(settings: PlayerSettings | null | undefined): number {
  return clamp(settings?.tempo_ratio ?? 1, TEMPO_RANGE.min, TEMPO_RANGE.max);
}

export function offsetOf(settings: PlayerSettings | null | undefined): number {
  return clampOffset(settings?.lyric_offset_ms ?? 0);
}

export function sameSettings(a: PlayerSettings, b: PlayerSettings): boolean {
  return (
    a.key_shift === b.key_shift &&
    a.tempo_ratio === b.tempo_ratio &&
    a.lyric_offset_ms === b.lyric_offset_ms &&
    JSON.stringify(a.stem_volumes ?? {}) === JSON.stringify(b.stem_volumes ?? {})
  );
}

/**
 * Coalesce rapid changes into one save.
 *
 * `flush` exists for the moment the page is being hidden: a debounce that only
 * ever fires on a timer loses the last change when someone closes the tab
 * immediately after making it, which is exactly when they were done adjusting.
 */
export function createSaver(
  save: (settings: PlayerSettings) => void,
  delay: number = SAVE_DEBOUNCE_MS,
): { schedule: (settings: PlayerSettings) => void; flush: () => void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending: PlayerSettings | null = null;

  const cancel = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };

  const flush = () => {
    cancel();
    if (pending === null) return;
    const settings = pending;
    pending = null;
    save(settings);
  };

  return {
    schedule(settings: PlayerSettings) {
      pending = settings;
      cancel();
      timer = setTimeout(flush, delay);
    },
    flush,
    cancel,
  };
}
