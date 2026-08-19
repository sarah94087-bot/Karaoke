"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { Dictionary } from "@/i18n";
import { type SongDetail, stemUrl } from "@/lib/api";
import { PlayerEngine, type PlayerState, type StemKind } from "@/lib/player/engine";
import { formatDuration } from "@/lib/song";

/**
 * The player, as far as T-1.12 goes: four stems on one clock, with transport.
 *
 * The mixer with its faders and the big "remove vocals" button are T-1.13, and
 * the key and tempo controls are T-1.14. What this screen proves is the thing
 * those depend on - that the engine loads, plays, seeks, and reports a position
 * that comes from the audio clock rather than from a timer.
 *
 * Note there is no setInterval here for the position. React re-renders because
 * the engine tells it to, which happens when the worklet reports, which happens
 * every 40 render quanta. Chapter 8 forbids browser timers for exactly this.
 */

const STEM_ORDER: StemKind[] = ["vocals", "drums", "bass", "other"];

export function Player({ song, t }: { song: SongDetail; t: Dictionary }) {
  const engineRef = useRef<PlayerEngine | null>(null);
  const [state, setState] = useState<PlayerState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (song.stems.length === 0) return;

    const engine = new PlayerEngine();
    engineRef.current = engine;
    const unsubscribe = engine.subscribe(setState);

    engine
      .load(song.stems.map((stem) => ({ kind: stem.kind, url: stemUrl(stem) })))
      .catch(() => setError(t.player.failed));

    return () => {
      unsubscribe();
      engine.dispose();
      engineRef.current = null;
    };
  }, [song.id, song.stems, t.player.failed]);

  const seek = useCallback((seconds: number) => engineRef.current?.seek(seconds), []);

  if (song.stems.length === 0) {
    return <p className="hint">{t.player.notReady}</p>;
  }
  if (error !== null) {
    return <p className="song-error">{error}</p>;
  }
  if (state === null || !state.ready) {
    return <p className="hint">{t.player.loading}</p>;
  }

  return (
    <div className="player">
      <div className="transport">
        <button type="button" onClick={() => void engineRef.current?.toggle()}>
          {state.playing ? t.player.pause : t.player.play}
        </button>
        <span className="clock" aria-live="off">
          {formatDuration(Math.floor(state.position))} /{" "}
          {formatDuration(Math.floor(state.duration))}
        </span>
      </div>

      <input
        className="scrubber"
        type="range"
        min={0}
        max={Math.max(1, Math.floor(state.duration))}
        step={1}
        value={Math.floor(state.position)}
        onChange={(event) => seek(Number(event.target.value))}
        aria-label={t.player.clock}
      />

      {/* The four channels are listed to make it visible that there are four of
          them on one engine. The faders arrive in T-1.13. */}
      <ul className="stem-list">
        {STEM_ORDER.filter((kind) => song.stems.some((stem) => stem.kind === kind)).map((kind) => (
          <li key={kind} className="stem-chip">
            {t.player.stems[kind]}
          </li>
        ))}
      </ul>
    </div>
  );
}
