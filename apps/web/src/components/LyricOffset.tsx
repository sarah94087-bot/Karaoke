"use client";

import type { Dictionary } from "@/i18n";
import { OFFSET_RANGE, OFFSET_STEP_MS } from "@/lib/lyrics";

/**
 * Nudging the whole song's words earlier or later (T-2.7).
 *
 * Buttons rather than a slider, for the reason T-1.14 gives about the key: this
 * is something people step until it fits, a hundred milliseconds at a time,
 * while watching one line go past. A slider is for scrubbing through a range;
 * this is not that.
 *
 * The number is part of the control and carries its sign explicitly, again like
 * the key: `+0.3s` is a change you made, where `0.3s` reads as a setting you
 * now have to remember the meaning of.
 *
 * Phase 0 is the reason this exists and also the reason it is not enough. The
 * systematic bias of a whole song measured +180ms, +540ms and -180ms on three
 * songs - so there is no global constant to bake in, and a per-song control is
 * needed. But the spread *within* one song reached a p90 of 1.7s, which no
 * single number can fix. T-2.9 is where that gets fixed, line by line.
 */

export function LyricOffset({
  offsetMs,
  onChange,
  t,
}: {
  offsetMs: number;
  onChange: (deltaMs: number) => void;
  t: Dictionary;
}) {
  const seconds = (offsetMs / 1000).toFixed(1);
  const signed = offsetMs > 0 ? `+${seconds}` : seconds;

  return (
    <div className="lyric-offset">
      <span className="lyric-offset-label">{t.lyrics.offset}</span>
      <div className="lyric-offset-buttons">
        <button
          type="button"
          onClick={() => onChange(-OFFSET_STEP_MS)}
          disabled={offsetMs <= OFFSET_RANGE.min}
          aria-label={t.lyrics.earlier}
          title={t.lyrics.earlier}
        >
          −
        </button>
        <span className="lyric-offset-value" aria-live="polite">
          {/* A number, so it stays left-to-right inside a Hebrew line - the same
              treatment the clock and the key get. */}
          <span className="ltr-number">{signed}</span> {t.lyrics.seconds}
        </span>
        <button
          type="button"
          onClick={() => onChange(OFFSET_STEP_MS)}
          disabled={offsetMs >= OFFSET_RANGE.max}
          aria-label={t.lyrics.later}
          title={t.lyrics.later}
        >
          +
        </button>
        <button
          type="button"
          className="lyric-offset-reset"
          onClick={() => onChange(-offsetMs)}
          disabled={offsetMs === 0}
        >
          {t.lyrics.reset}
        </button>
      </div>
    </div>
  );
}
