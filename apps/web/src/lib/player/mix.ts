/**
 * The mixer's state, kept apart from the component so it can be tested.
 *
 * Chapter 8 asks for "4 faders + a big 'remove vocals' button". The button is
 * not a fifth fader: it is the shortcut for the thing people actually opened
 * the app to do, and the only interesting behaviour in this file is what
 * happens when it and the vocals fader disagree.
 *
 * The rule: pressing it twice puts you back exactly where you were. Somebody
 * who had the vocals down at 20% as a guide track and taps the button to hear
 * the line again expects 20% back, not full volume.
 */

import type { StemKind } from "./engine.ts";

export const STEM_ORDER: readonly StemKind[] = ["vocals", "drums", "bass", "other"] as const;

export interface MixState {
  volumes: Record<StemKind, number>;
  /** True when the vocals are silent, however they got that way. */
  vocalsRemoved: boolean;
  /** What to put the vocals back to. Never 0, or the button would be a no-op. */
  restoreVocalsTo: number;
}

export const DEFAULT_MIX: MixState = {
  volumes: { vocals: 1, drums: 1, bass: 1, other: 1 },
  vocalsRemoved: false,
  restoreVocalsTo: 1,
};

function clamp(volume: number): number {
  if (Number.isNaN(volume)) return 0;
  return Math.max(0, Math.min(1, volume));
}

export function setStemVolume(mix: MixState, kind: StemKind, volume: number): MixState {
  const level = clamp(volume);
  const volumes = { ...mix.volumes, [kind]: level };

  if (kind !== "vocals") return { ...mix, volumes };

  // Dragging the vocals fader to zero *is* removing the vocals, so the button
  // has to agree with the fader rather than contradict it.
  return {
    volumes,
    vocalsRemoved: level === 0,
    restoreVocalsTo: level > 0 ? level : mix.restoreVocalsTo,
  };
}

export function toggleVocals(mix: MixState): MixState {
  if (mix.vocalsRemoved) {
    const level = mix.restoreVocalsTo > 0 ? mix.restoreVocalsTo : 1;
    return { ...mix, volumes: { ...mix.volumes, vocals: level }, vocalsRemoved: false };
  }
  return {
    volumes: { ...mix.volumes, vocals: 0 },
    vocalsRemoved: true,
    restoreVocalsTo: mix.volumes.vocals > 0 ? mix.volumes.vocals : mix.restoreVocalsTo,
  };
}

/** Whole percent, for the label next to a fader. */
export function asPercent(volume: number): number {
  return Math.round(clamp(volume) * 100);
}
