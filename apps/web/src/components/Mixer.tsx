"use client";

import type { Dictionary } from "@/i18n";
import type { StemMode } from "@/lib/player/capability";
import type { Channel, StemKind } from "@/lib/player/engine";
import { type MixState, asPercent, backingVolume, fadersFor } from "@/lib/player/mix";

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
  mode,
  t,
  onVolume,
  onToggleVocals,
  onMode,
}: {
  mix: MixState;
  available: readonly StemKind[];
  mode: StemMode;
  t: Dictionary;
  onVolume: (kind: Channel, volume: number) => void;
  onToggleVocals: () => void;
  onMode: (mode: StemMode) => void;
}) {
  const faders = fadersFor(mode, available);
  const levelOf = (kind: Channel) =>
    kind === "backing" ? backingVolume(mix) : mix.volumes[kind as StemKind];

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

      {/*
        A control rather than only an automatic decision. The measurement that
        would choose this reliably needs real phone hardware (T-0.2.5, still
        blocked), and an automatic answer nobody can override is a mystery when
        it is wrong in either direction. One tap either way.
      */}
      <div className="mode-switch">
        <label>
          <input
            type="checkbox"
            checked={mode === "two"}
            onChange={(event) => onMode(event.target.checked ? "two" : "four")}
          />
          <span>{t.mixer.lightMode}</span>
        </label>
        <span className="hint">{mode === "two" ? t.mixer.twoStems : t.mixer.lightModeOff}</span>
      </div>

      <ul className="faders">
        {faders.map((kind) => (
          <li key={kind} className="fader" data-silent={levelOf(kind) === 0}>
            <label>
              <span className="fader-name">{t.player.stems[kind]}</span>
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={asPercent(levelOf(kind))}
                onChange={(event) => onVolume(kind, Number(event.target.value) / 100)}
              />
            </label>
            <span className="fader-value">{asPercent(levelOf(kind))}%</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
