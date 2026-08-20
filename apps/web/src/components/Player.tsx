"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { KeyTempo } from "@/components/KeyTempo";
import { Mixer } from "@/components/Mixer";
import type { Dictionary } from "@/i18n";
import { type SongDetail, saveSettings, stemUrl } from "@/lib/api";
import { PlayerEngine, type PlayerState, type StemKind } from "@/lib/player/engine";
import { type MixState, setStemVolume, toggleVocals } from "@/lib/player/mix";
import { createSaver, keyOf, tempoOf, toMix, toSettings } from "@/lib/player/persist";
import { formatDuration } from "@/lib/song";

/**
 * The player, as far as T-1.12 goes: four stems on one clock, with transport.
 *
 * What this screen holds together is the engine (T-1.12), the mixer (T-1.13)
 * and the key and tempo controls (T-1.14). Key, tempo and volume all read their
 * displayed value from the engine's state rather than from local copies, so
 * there is nothing for the two to disagree about.
 *
 * Note there is no setInterval here for the position. React re-renders because
 * the engine tells it to, which happens when the worklet reports, which happens
 * every 40 render quanta. Chapter 8 forbids browser timers for exactly this.
 */

export function Player({ song, t }: { song: SongDetail; t: Dictionary }) {
  const engineRef = useRef<PlayerEngine | null>(null);
  const [state, setState] = useState<PlayerState | null>(null);
  // Seeded from what was saved, so the first render already shows the key and
  // mix this person left the song in rather than flashing the defaults.
  const [mix, setMix] = useState<MixState>(() => toMix(song.settings));
  const [error, setError] = useState<string | null>(null);

  /**
   * Chapter 5 says settings are "saved automatically on every change in the
   * player". Taken literally that is one request per pixel of fader drag, so
   * changes are coalesced: every *intent* is saved, once the hand stops moving.
   */
  const saver = useRef(
    createSaver((settings) => {
      void saveSettings(song.id, settings).catch(() => {
        // A failed save is not worth interrupting someone mid-song for. The
        // next change tries again, and the settings are a convenience.
      });
    }),
  );

  useEffect(() => {
    if (song.stems.length === 0) return;

    const engine = new PlayerEngine();
    engineRef.current = engine;
    const unsubscribe = engine.subscribe(setState);

    // Applied before load so the graph is built at the saved key and mix, not
    // adjusted to it a moment later - which would be audible.
    const saved = toMix(song.settings);
    engine.setKey(keyOf(song.settings));
    engine.setTempo(tempoOf(song.settings));
    for (const [kind, volume] of Object.entries(saved.volumes)) {
      engine.setVolume(kind as StemKind, volume);
    }

    engine
      .load(song.stems.map((stem) => ({ kind: stem.kind, url: stemUrl(stem) })))
      .then(() => {
        // load() builds the gain nodes, so the volumes have to be pushed again
        // for them to take; the engine remembers them across a load for exactly
        // this reason.
        for (const [kind, volume] of Object.entries(saved.volumes)) {
          engine.setVolume(kind as StemKind, volume);
        }
      })
      .catch(() => setError(t.player.failed));

    return () => {
      unsubscribe();
      engine.dispose();
      engineRef.current = null;
    };
  }, [song.id, song.stems, song.settings, t.player.failed]);

  /**
   * Write whatever is pending when the page is being hidden. A debounce that
   * only fires on a timer loses the last change when someone closes the tab
   * straight after making it - which is exactly when they had finished
   * adjusting.
   */
  useEffect(() => {
    const pending = saver.current;
    const flush = () => pending.flush();
    document.addEventListener("visibilitychange", flush);
    window.addEventListener("pagehide", flush);
    return () => {
      document.removeEventListener("visibilitychange", flush);
      window.removeEventListener("pagehide", flush);
      pending.flush();
    };
  }, []);

  const seek = useCallback((seconds: number) => engineRef.current?.seek(seconds), []);

  /**
   * The mix state is the source of truth for the UI; the engine is told about
   * every stem afterwards. Applying the whole mix rather than the one fader
   * that moved keeps the two from drifting apart - "remove vocals" changes one
   * fader, but nothing here has to know that.
   */
  const applyMix = useCallback(
    (next: MixState) => {
      setMix(next);
      const engine = engineRef.current;
      if (engine === null) return;
      for (const [kind, volume] of Object.entries(next.volumes)) {
        engine.setVolume(kind as StemKind, volume);
      }
      saver.current.schedule(toSettings(next, engine.getState().semitones, engine.getState().tempo));
    },
    [],
  );

  const applyKey = useCallback(
    (semitones: number) => {
      const engine = engineRef.current;
      if (engine === null) return;
      engine.setKey(semitones);
      saver.current.schedule(toSettings(mix, engine.getState().semitones, engine.getState().tempo));
    },
    [mix],
  );

  const applyTempo = useCallback(
    (ratio: number) => {
      const engine = engineRef.current;
      if (engine === null) return;
      engine.setTempo(ratio);
      saver.current.schedule(toSettings(mix, engine.getState().semitones, engine.getState().tempo));
    },
    [mix],
  );

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

      <KeyTempo
        semitones={state.semitones}
        tempo={state.tempo}
        t={t}
        onKey={applyKey}
        onTempo={applyTempo}
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
