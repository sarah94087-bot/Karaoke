"use client";

import type { Dictionary } from "@/i18n";
import { type Loop, isLooping } from "@/lib/player/loop";

function clock(seconds: number): string {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/**
 * Marking a section and repeating it (T-5.2).
 *
 * Two buttons rather than a drag on the timeline, for the reason T-1.14 gave
 * about the key: the marks are made *while listening*, one at a time, with
 * attention on the music - not by aiming at a pixel. The playhead is already
 * where the ear is.
 */
export function LoopControls({
  loop,
  onStart,
  onEnd,
  onClear,
  t,
}: {
  loop: Loop;
  onStart: () => void;
  onEnd: () => void;
  onClear: () => void;
  t: Dictionary;
}) {
  const active = isLooping(loop);

  return (
    <div className="loop-controls" data-active={active}>
      <div className="loop-buttons">
        <button type="button" onClick={onStart}>
          {t.player.loop.markStart}
        </button>
        <button type="button" onClick={onEnd}>
          {t.player.loop.markEnd}
        </button>
        <button type="button" onClick={onClear} disabled={loop.a === null && loop.b === null}>
          {t.player.loop.clear}
        </button>
      </div>
      <p className="loop-state">
        {active ? (
          <>
            {t.player.loop.repeating}{" "}
            {/* LTR-isolated, or "0:42 – 0:55" renders backwards inside a
                Hebrew line - the same trap T-1.10 hit with durations. */}
            <span className="ltr-number">
              {clock(loop.a)} – {clock(loop.b)}
            </span>
          </>
        ) : loop.a !== null ? (
          t.player.loop.needEnd
        ) : (
          t.player.loop.hint
        )}
      </p>
    </div>
  );
}
