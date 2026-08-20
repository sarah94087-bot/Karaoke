"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Mixer } from "@/components/Mixer";
import type { Dictionary } from "@/i18n";
import { type SongDetail, stemUrl } from "@/lib/api";
import { PlayerEngine, type PlayerState, type StemKind } from "@/lib/player/engine";
import { DEFAULT_MIX, type MixState, setStemVolume, toggleVocals } from "@/lib/player/mix";
import { formatDuration } from "@/lib/song";

/**
 * The player, as far as T-1.12 goes: four stems on one clock, with transport.
 *
 * The key and tempo controls are T-1.14. What this screen holds together is the
 * engine (T-1.12) and the mixer (T-1.13): it loads, plays, seeks, and reports a
 * position that comes from the audio clock rather than from a timer.
 *
 * Note there is no setInterval here for the position. React re-renders because
 * the engine tells it to, which happens when the worklet reports, which happens
 * every 40 render quanta. Chapter 8 forbids browser timers for exactly this.
 */

export function Player({ song, t }: { song: SongDetail; t: Dictionary }) {
  const engineRef = useRef<PlayerEngine | null>(null);
  const [state, setState] = useState<PlayerState | null>(null);
  const [mix, setMix] = useState<MixState>(DEFAULT_MIX);
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

  /**
   * The mix state is the source of truth for the UI; the engine is told about
   * every stem afterwards. Applying the whole mix rather than the one fader
   * that moved keeps the two from drifting apart - "remove vocals" changes one
   * fader, but nothing here has to know that.
   */
  const applyMix = useCallback((next: MixState) => {
    setMix(next);
    const engine = engineRef.current;
    if (engine === null) return;
    for (const [kind, volume] of Object.entries(next.volumes)) {
      engine.setVolume(kind as StemKind, volume);
    }
  }, []);

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

      <Mixer
        mix={mix}
        available={song.stems.map((stem) => stem.kind)}
        t={t}
        onVolume={(kind, volume) => applyMix(setStemVolume(mix, kind, volume))}
        onToggleVocals={() => applyMix(toggleVocals(mix))}
      />
    </div>
  );
}
