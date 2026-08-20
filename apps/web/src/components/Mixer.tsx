"use client";

import type { Dictionary } from "@/i18n";
import type { StemKind } from "@/lib/player/engine";
import { type MixState, STEM_ORDER, asPercent } from "@/lib/player/mix";

/**
 * Four faders and the big button.
 *
 * The button is deliberately not a fifth fader and not a small icon: chapter 8
 * calls it "a big 'remove vocals' button", and it is the shortcut for the thing
 * people came to do. Everything else on this screen is an adjustment; this is
 * the feature.
 *
 * Changing a fader goes straight to a GainNode hanging off the worklet's output
 * (T-0.2.2), so it never touches the engine and cannot cost sync - which is why
 * it is safe to do mid-song, and why there is no "apply" step.
 */
export function Mixer({
  mix,
  available,
  t,
  onVolume,
  onToggleVocals,
}: {
  mix: MixState;
  available: readonly StemKind[];
  t: Dictionary;
  onVolume: (kind: StemKind, volume: number) => void;
  onToggleVocals: () => void;
}) {
  const faders = STEM_ORDER.filter((kind) => available.includes(kind));

  return (
    <section className="mixer">
      <button
        type="button"
        className="remove-vocals"
        onClick={onToggleVocals}
        aria-pressed={mix.vocalsRemoved}
      >
        {mix.vocalsRemoved ? t.mixer.restoreVocals : t.mixer.removeVocals}
      </button>

      <ul className="faders">
        {faders.map((kind) => (
          <li key={kind} className="fader" data-silent={mix.volumes[kind] === 0}>
            <label>
              <span className="fader-name">{t.player.stems[kind]}</span>
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={asPercent(mix.volumes[kind])}
                onChange={(event) => onVolume(kind, Number(event.target.value) / 100)}
              />
            </label>
            <span className="fader-value">{asPercent(mix.volumes[kind])}%</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
