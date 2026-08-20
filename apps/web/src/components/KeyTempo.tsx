"use client";

import type { Dictionary } from "@/i18n";
import { KEY_RANGE, TEMPO_RANGE } from "@/lib/player/engine";
import {
  DEFAULT_KEY,
  DEFAULT_TEMPO,
  TEMPO_STEP,
  canLowerKey,
  canRaiseKey,
  formatKey,
  formatTempo,
  isDefaultKey,
  isDefaultTempo,
  stepKey,
} from "@/lib/player/controls";

/**
 * Key and tempo (chapter 8: -6..+6 semitones, 50%-150%).
 *
 * Both take effect on the next frame boundary inside the worklet, so there is
 * no "apply" and no reload - and no reason for one. The value is shown next to
 * each control because the acceptance criterion asks for it, and because
 * "somewhere around plus two" is not a key you can return to tomorrow.
 *
 * Key gets buttons and tempo gets a slider on purpose. A key is thirteen
 * discrete choices and people move it one semitone at a time until their voice
 * fits; a tempo is continuous and people scrub for it.
 */
export function KeyTempo({
  semitones,
  tempo,
  t,
  onKey,
  onTempo,
}: {
  semitones: number;
  tempo: number;
  t: Dictionary;
  onKey: (semitones: number) => void;
  onTempo: (ratio: number) => void;
}) {
  return (
    <section className="key-tempo">
      <div className="control">
        <div className="control-head">
          <span className="control-name">{t.controls.key}</span>
          <span className="control-value" aria-live="polite">
            {formatKey(semitones)}
          </span>
        </div>
        <div className="stepper">
          <button
            type="button"
            onClick={() => onKey(stepKey(semitones, -1))}
            disabled={!canLowerKey(semitones)}
            aria-label={t.controls.keyDown}
          >
            −
          </button>
          <input
            type="range"
            min={KEY_RANGE.min}
            max={KEY_RANGE.max}
            step={1}
            value={semitones}
            onChange={(event) => onKey(Number(event.target.value))}
            aria-label={t.controls.key}
          />
          <button
            type="button"
            onClick={() => onKey(stepKey(semitones, 1))}
            disabled={!canRaiseKey(semitones)}
            aria-label={t.controls.keyUp}
          >
            +
          </button>
        </div>
        {!isDefaultKey(semitones) ? (
          <button type="button" className="reset" onClick={() => onKey(DEFAULT_KEY)}>
            {t.controls.reset}
          </button>
        ) : null}
      </div>

      <div className="control">
        <div className="control-head">
          <span className="control-name">{t.controls.tempo}</span>
          <span className="control-value" aria-live="polite">
            {formatTempo(tempo)}
          </span>
        </div>
        <input
          type="range"
          min={TEMPO_RANGE.min}
          max={TEMPO_RANGE.max}
          step={TEMPO_STEP}
          value={tempo}
          onChange={(event) => onTempo(Number(event.target.value))}
          aria-label={t.controls.tempo}
        />
        {!isDefaultTempo(tempo) ? (
          <button type="button" className="reset" onClick={() => onTempo(DEFAULT_TEMPO)}>
            {t.controls.reset}
          </button>
        ) : null}
      </div>
    </section>
  );
}
