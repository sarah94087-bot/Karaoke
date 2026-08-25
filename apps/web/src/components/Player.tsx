"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { KeyTempo } from "@/components/KeyTempo";
import { LyricOffset } from "@/components/LyricOffset";
import { Lyrics } from "@/components/Lyrics";
import { Mixer } from "@/components/Mixer";
import type { Dictionary } from "@/i18n";
import {
  type LyricLine,
  type SongDetail,
  getLyrics,
  isPending,
  saveSettings,
  markPlayed,
  stemUrl,
} from "@/lib/api";
import { LoopControls } from "@/components/LoopControls";
import { Shortcuts } from "@/components/Shortcuts";
import { directionOf, isLocale } from "@/i18n/config";
import { type PlayerAction, actionFor } from "@/lib/player/keys";
import {
  EMPTY_QUEUE,
  QUEUE_EVENT,
  type Queue,
  loadQueue,
  nextAfter,
  refresh,
  storeQueue,
} from "@/lib/player/queue";
import { type StemMode, resolveMode, storeMode } from "@/lib/player/capability";
import {
  type Loop,
  LOOP_CHECK_MS,
  NO_LOOP,
  clearLoop,
  crossedEnd,
  loopBand,
  markEnd,
  markStart,
  wrapTo,
} from "@/lib/player/loop";
import {
  type Channel,
  PlayerEngine,
  type PlayerState,
  type StemKind,
} from "@/lib/player/engine";
import {
  type MixState,
  channelVolumes,
  setBackingVolume,
  setStemVolume,
  toggleVocals,
} from "@/lib/player/mix";
import { stepOffset } from "@/lib/lyrics";
import { createSaver, keyOf, offsetOf, tempoOf, toMix, toSettings } from "@/lib/player/persist";
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

export function Player({
  song,
  lyrics: initialLyrics,
  locale,
  t,
}: {
  song: SongDetail;
  /** Fetched on the server with the song, so the words are on the first paint
   *  rather than one round trip later. Null when there are none yet. */
  lyrics: LyricLine[] | null;
  /** T-5.1: moving to the next song in the queue is a navigation, and a
   *  navigation in this app needs to know which language it is in. */
  locale: string;
  t: Dictionary;
}) {
  const router = useRouter();
  const shellRef = useRef<HTMLDivElement | null>(null);
  const engineRef = useRef<PlayerEngine | null>(null);
  const [state, setState] = useState<PlayerState | null>(null);
  // Seeded from what was saved, so the first render already shows the key and
  // mix this person left the song in rather than flashing the defaults.
  const [mix, setMix] = useState<MixState>(() => toMix(song.settings));
  const [mode, setMode] = useState<StemMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lyrics, setLyrics] = useState<{
    lines: LyricLine[];
    status: SongDetail["lyrics_status"];
  }>({ lines: initialLyrics ?? [], status: song.lyrics_status });
  // T-2.7. State rather than a prop read: a nudge has to move the words on the
  // screen now, not after a reload.
  const [offsetMs, setOffsetMs] = useState(() => offsetOf(song.settings));
  // T-5.2. Not saved with the settings: a practice section belongs to the
  // half hour someone spends on one line, not to the song for ever.
  const [loop, setLoop] = useState<Loop>(NO_LOOP);
  // The wrap is watched on the audio clock in an animation frame, which cannot
  // see React state - the same refs pattern the lyrics editor uses.
  const loopRef = useRef(loop);
  loopRef.current = loop;
  // T-5.1. The evening's running order, and the two ways this screen is bigger
  // than one song: full screen, and the shortcut list.
  const [queue, setQueue] = useState<Queue>(EMPTY_QUEUE);
  const [cinema, setCinema] = useState(false);
  const [help, setHelp] = useState(false);

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

  /**
   * Chapter 8's third hard requirement: fall back to two stems when four plus
   * pitch shifting is too heavy. See capability.ts for why the automatic part
   * of this is deliberately timid and the switch in the mixer carries the
   * requirement. Resolved once, before the engine is built - changing mode
   * rebuilds the graph, which is why it is not something to do casually.
   */
  useEffect(() => {
    let cancelled = false;
    resolveMode().then((capability) => {
      if (!cancelled) setMode(capability.mode);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const chooseMode = useCallback((next: StemMode) => {
    storeMode(next);
    setMode(next);
  }, []);

  useEffect(() => {
    if (song.stems.length === 0 || mode === null) return;

    const engine = new PlayerEngine();
    engineRef.current = engine;
    const unsubscribe = engine.subscribe(setState);

    // Applied before load so the graph is built at the saved key and mix, not
    // adjusted to it a moment later - which would be audible.
    const saved = toMix(song.settings);
    const applyVolumes = () => {
      for (const [kind, volume] of Object.entries(channelVolumes(saved, mode))) {
        engine.setVolume(kind as Channel, volume);
      }
    };
    engine.setKey(keyOf(song.settings));
    engine.setTempo(tempoOf(song.settings));
    applyVolumes();

    engine
      .load(song.stems.map((stem) => ({ kind: stem.kind, url: stemUrl(stem) })), { mode })
      // load() builds the gain nodes, so the volumes have to be pushed again
      // for them to take; the engine remembers them across a load for exactly
      // this reason.
      .then(applyVolumes)
      .catch(() => setError(t.player.failed));

    return () => {
      unsubscribe();
      engine.dispose();
      engineRef.current = null;
    };
  }, [song.id, song.stems, song.settings, mode, t.player.failed]);

  /**
   * The words, and then the better words.
   *
   * D-28 opens this screen before the lyrics exist, so "not yet" is a normal
   * answer (a 202) and the right response to it is to ask again rather than to
   * draw a failure. The pipeline replaces a stand-in transcript with the real
   * one part-way through (T-2.4), which is why this keeps asking while the song
   * is still processing rather than stopping at the first answer with lines in
   * it - the words improve under the singer, mid-song, which is chapter 8's
   * "lyrics on the way" working as designed.
   */
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const ask = async () => {
      try {
        const body = await getLyrics(song.id);
        if (cancelled) return;
        if (isPending(body)) {
          setLyrics({ lines: [], status: "pending" });
        } else {
          setLyrics({ lines: body.lines, status: body.status });
          if (body.status !== "pending" && song.status === "ready") return;
        }
      } catch {
        // A lyrics fetch that fails is not worth breaking the player for: the
        // stems are already playing. The next poll tries again.
        if (cancelled) return;
      }
      // Slow on purpose. The words arrive once, somewhere in the minute after
      // the stems, and a tighter poll would spend a request a second on a free
      // tier for a screen that is already busy playing audio.
      timer = setTimeout(() => void ask(), 5_000);
    };

    void ask();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [song.id, song.status]);

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

  /**
   * The queue, read from the device (T-5.1).
   *
   * Read here rather than passed in because this page is server-rendered and
   * the queue is a thing of this browser: it has to be the same list the
   * library screen wrote, and the only place both can see is storage.
   *
   * The stored title is corrected against the song actually opened, so a
   * rename (T-4.2) does not leave the evening announcing a name that no longer
   * exists.
   */
  useEffect(() => {
    const read = () => setQueue(loadQueue());
    read();
    window.addEventListener(QUEUE_EVENT, read);
    window.addEventListener("storage", read);
    return () => {
      window.removeEventListener(QUEUE_EVENT, read);
      window.removeEventListener("storage", read);
    };
  }, []);

  useEffect(() => {
    const current = loadQueue();
    const corrected = refresh(current, { id: song.id, title: song.title });
    if (corrected !== current) storeQueue(corrected);
  }, [song.id, song.title]);

  const next = nextAfter(queue, song.id);

  /**
   * Go to the next song, playing.
   *
   * `autoplay=1` is in the address rather than in storage because it belongs
   * to this one navigation. The player strips it as soon as it has acted on
   * it, so a reload an hour later opens the song silently.
   */
  const goNext = useCallback(() => {
    if (next === null) return;
    router.push(`/${locale}/songs/${next.id}?autoplay=1`);
  }, [locale, next, router]);

  const seek = useCallback((seconds: number) => engineRef.current?.seek(seconds), []);

  /**
   * Play or pause, and tell the API the first time this song is actually sung.
   *
   * Once per visit, not per press: chapter 9 deletes songs nobody has played
   * for six months and D-30 offers up the least played, so this is the fact
   * both rules rest on - and pausing to find your place is not a second play.
   */
  const played = useRef(false);
  const startOrPause = useCallback(async () => {
    const engine = engineRef.current;
    if (engine === null) return;
    const starting = !engine.getState().playing;
    await engine.toggle();
    if (starting && !played.current) {
      played.current = true;
      void markPlayed(song.id);
    }
  }, [song.id]);

  /**
   * The end of a song is the start of the next one (T-5.1).
   *
   * This is the whole of "run an evening without touching the mouse": the
   * worklet says `ended`, which the engine keeps separate from `playing:
   * false` precisely so that a pause is not mistaken for a finish, and the
   * queue says what follows. A song that is not in the queue ends the way it
   * always has - nobody who opened one song from the library is dropped into
   * somebody else's running order.
   */
  useEffect(() => {
    if (state?.ended !== true) return;
    goNext();
  }, [state?.ended, goNext]);

  /**
   * Arriving with the music expected to start.
   *
   * The gesture rule is what makes this work at all: a browser will not let a
   * page make noise on its own, but the *document* keeps its activation across
   * a client-side navigation, so the play press that started song one is still
   * good for song two. A full reload is not, and the honest response to that
   * is to sit paused with the transport waiting rather than to pretend.
   */
  const autoplayed = useRef(false);
  useEffect(() => {
    if (state === null || !state.ready || autoplayed.current) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("autoplay") !== "1") return;
    autoplayed.current = true;
    // Once only: the address is cleaned so a reload later in the evening does
    // not restart the song by itself.
    params.delete("autoplay");
    const query = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (query ? `?${query}` : ""));
    void startOrPause();
  }, [state?.ready, startOrPause]);

  /**
   * Full screen: our own layout first, the browser's if it will.
   *
   * The class is applied whether or not `requestFullscreen` works, and that
   * order is deliberate - iOS Safari has no fullscreen for an element, and a
   * feature that simply does nothing there would be the worst of the three
   * possible outcomes. What the browser adds when it can is hiding its own
   * chrome; what the class does everywhere is give the words the screen.
   */
  const toggleCinema = useCallback(() => {
    setCinema((on) => {
      if (on) {
        if (document.fullscreenElement !== null) void document.exitFullscreen().catch(() => {});
      } else {
        void shellRef.current?.requestFullscreen?.().catch(() => {
          // No element fullscreen here. The class still applies.
        });
        // Focus moves to the shell so the shortcuts land on the page rather
        // than on whatever button was last pressed.
        shellRef.current?.focus();
      }
      return !on;
    });
  }, []);

  // Escape leaves the browser's fullscreen without telling this component, and
  // a screen that stays in its cinema layout after that looks broken.
  useEffect(() => {
    const sync = () => {
      if (document.fullscreenElement === null) setCinema(false);
    };
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  /**
   * The loop: the audio clock decides *where* we are, and two things ask.
   *
   * Chapter 8's rule holds - the position always comes from `positionNow()`,
   * which is the engine's own clock, never from counting elapsed milliseconds.
   * What changes here is only how often somebody asks.
   *
   * `requestAnimationFrame` asks once a frame, which is the precise answer
   * while the singer is looking at the screen. But a browser freezes frames in
   * a hidden tab: measured in Chrome, **zero frames in two seconds** with the
   * audio still playing. For the lyrics that is harmless - nobody is reading
   * them. For a loop it is not: the section would quietly stop repeating and
   * the song would run on, which is a change to what the user *hears* while
   * they are not looking. So an interval asks as well, and whichever gets
   * there first wraps.
   *
   * The honest limit: browsers clamp timers in a hidden tab to about a second,
   * so a loop the user cannot see may overshoot by that much before it comes
   * back. Sample-accurate wrapping belongs in the worklet, and T-1.12 is
   * explicit that editing that file means re-earning phase 0's drift
   * measurements - not something to spend on a tab nobody is watching.
   */
  useEffect(() => {
    if (state === null || !state.ready) return;
    let previous = engineRef.current?.positionNow() ?? 0;
    const check = () => {
      const engine = engineRef.current;
      if (engine === null || !engine.getState().playing) return;
      const now = engine.positionNow();
      if (crossedEnd(loopRef.current, previous, now)) {
        engine.seek(wrapTo(loopRef.current));
        previous = wrapTo(loopRef.current);
      } else {
        previous = now;
      }
    };

    let frame = 0;
    const tick = () => {
      check();
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    const timer = window.setInterval(check, LOOP_CHECK_MS);
    return () => {
      cancelAnimationFrame(frame);
      window.clearInterval(timer);
    };
  }, [state?.ready]);

  const markLoopStart = useCallback(() => {
    const engine = engineRef.current;
    if (engine === null) return;
    setLoop((current) => markStart(current, engine.positionNow(), engine.getState().duration));
  }, []);

  const markLoopEnd = useCallback(() => {
    const engine = engineRef.current;
    if (engine === null) return;
    setLoop((current) => markEnd(current, engine.positionNow(), engine.getState().duration));
  }, []);

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
      for (const [kind, volume] of Object.entries(channelVolumes(next, mode ?? "four"))) {
        engine.setVolume(kind as Channel, volume);
      }
      saver.current.schedule(
        toSettings(next, engine.getState().semitones, engine.getState().tempo, offsetMs),
      );
    },
    [mode, offsetMs],
  );

  const applyKey = useCallback(
    (semitones: number) => {
      const engine = engineRef.current;
      if (engine === null) return;
      engine.setKey(semitones);
      saver.current.schedule(
        toSettings(mix, engine.getState().semitones, engine.getState().tempo, offsetMs),
      );
    },
    [mix, offsetMs],
  );

  const applyTempo = useCallback(
    (ratio: number) => {
      const engine = engineRef.current;
      if (engine === null) return;
      engine.setTempo(ratio);
      saver.current.schedule(
        toSettings(mix, engine.getState().semitones, engine.getState().tempo, offsetMs),
      );
    },
    [mix, offsetMs],
  );

  /**
   * Nudge the words, and remember it.
   *
   * Saved the same way every other player setting is (T-1.16): coalesced, so
   * holding the button down is one request rather than one per press, and
   * flushed when the tab is hidden.
   */
  const nudgeLyrics = useCallback(
    (deltaMs: number) => {
      const next = stepOffset(offsetMs, deltaMs);
      setOffsetMs(next);
      const engine = engineRef.current;
      saver.current.schedule(
        toSettings(mix, engine?.getState().semitones ?? 0, engine?.getState().tempo ?? 1, next),
      );
    },
    [mix, offsetMs],
  );

  /**
   * The keyboard (T-5.1), which is what makes the evening mouse-free.
   *
   * The handler is attached once and reads the *current* dispatch through a
   * ref that is rewritten on every render. That is not decoration: T-2.9 lost
   * a nudge to exactly this, two presses inside one render both computing from
   * the same stale array. A listener registered once with a closure over `mix`
   * and `state` would go stale the same way, silently, and only for the person
   * using the keys rather than the buttons.
   */
  const dispatchRef = useRef<(action: PlayerAction) => void>(() => {});
  dispatchRef.current = (action: PlayerAction) => {
    const engine = engineRef.current;
    switch (action.type) {
      case "toggle":
        void startOrPause();
        break;
      case "seek":
        if (engine !== null) engine.seek(engine.positionNow() + action.seconds);
        break;
      case "key":
        if (engine !== null) applyKey(engine.getState().semitones + action.steps);
        break;
      case "tempo":
        if (engine !== null) applyTempo(engine.getState().tempo + action.delta);
        break;
      case "vocals":
        applyMix(toggleVocals(mix));
        break;
      case "next":
        goNext();
        break;
      case "fullscreen":
        toggleCinema();
        break;
      case "loopStart":
        markLoopStart();
        break;
      case "loopEnd":
        markLoopEnd();
        break;
      case "loopClear":
        setLoop(clearLoop());
        break;
      case "help":
        setHelp((open) => !open);
        break;
    }
  };

  const dir = isLocale(locale) ? directionOf(locale) : "rtl";

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const action = actionFor(
        {
          code: event.code,
          key: event.key,
          ctrlKey: event.ctrlKey,
          metaKey: event.metaKey,
          altKey: event.altKey,
          target: event.target as HTMLElement | null,
        },
        dir,
      );
      if (action === null) return;
      // Space scrolls the page and the arrows scroll it too. A shortcut that
      // also moved the screen would be unusable on the one screen it matters.
      event.preventDefault();
      dispatchRef.current(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dir]);

  /*
   * The words do not wait for the audio. Decoding four stems takes a moment,
   * and the lyrics are the thing on this screen that can be read without a
   * clock - so they are painted first and start moving when the engine does.
   * The same reasoning as D-28, one level down.
   */
  const words = (
    <Lyrics
      lines={lyrics.lines}
      status={lyrics.status}
      engine={state?.ready ? engineRef.current : null}
      offsetMs={offsetMs}
      playing={state?.playing ?? false}
      t={t}
    />
  );

  if (song.stems.length === 0) {
    return <p className="hint">{t.player.notReady}</p>;
  }
  if (error !== null) {
    return <p className="song-error">{error}</p>;
  }
  if (mode === null || state === null || !state.ready) {
    return (
      <div className="player">
        {words}
        <p className="hint">{t.player.loading}</p>
      </div>
    );
  }

  const band = loopBand(loop, state.duration);

  return (
    // tabIndex so the shell itself can hold focus in full screen: the
    // shortcuts are ignored while a form control has the focus, and something
    // has to be focused.
    <div className="player" ref={shellRef} tabIndex={-1} data-cinema={cinema}>
      <div className="stage-bar">
        <button type="button" onClick={toggleCinema}>
          {cinema ? t.player.fullscreen.exit : t.player.fullscreen.enter}
        </button>
        {/* The next song is named rather than merely counted: knowing what is
            coming is what lets somebody decide to skip it before it starts. */}
        {next !== null ? (
          <button type="button" className="skip" onClick={goNext}>
            {t.queue.next}: {next.title}
          </button>
        ) : null}
        <button type="button" className="ghost" onClick={() => setHelp((open) => !open)}>
          {t.player.shortcuts.show}
        </button>
      </div>

      {help ? <Shortcuts t={t} onClose={() => setHelp(false)} /> : null}

      <div className="transport">
        <button type="button" onClick={() => void startOrPause()}>
          {state.playing ? t.player.pause : t.player.play}
        </button>
        <span className="clock" aria-live="off">
          {formatDuration(Math.floor(state.position))} /{" "}
          {formatDuration(Math.floor(state.duration))}
        </span>
      </div>

      <div className="timeline">
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
        {/* The marked section, drawn under the scrubber. Left to right always
            means start to end, whatever the page direction, which is why this
            band is its own LTR element rather than a background on the input. */}
        {band !== null ? (
          <div className="loop-band" aria-hidden="true">
            <span style={{ insetInlineStart: `${band.from}%`, inlineSize: `${band.to - band.from}%` }} />
          </div>
        ) : null}
      </div>

      {/* Everything below is hidden in full screen rather than unmounted: it
          is still on the keyboard, and remounting the mixer on every press of
          F would throw away nothing useful and cost a render of five faders. */}
      <div className="when-windowed">
        <LoopControls
          loop={loop}
          onStart={markLoopStart}
          onEnd={markLoopEnd}
          onClear={() => setLoop(clearLoop())}
          t={t}
        />
      </div>

      {words}

      <div className="when-windowed">
        {lyrics.lines.length > 0 ? (
          <LyricOffset offsetMs={offsetMs} onChange={nudgeLyrics} t={t} />
        ) : null}

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
          mode={mode}
          t={t}
          onVolume={(kind, volume) =>
            applyMix(
              kind === "backing"
                ? setBackingVolume(mix, volume)
                : setStemVolume(mix, kind as StemKind, volume),
            )
          }
          onToggleVocals={() => applyMix(toggleVocals(mix))}
          onMode={chooseMode}
        />
      </div>
    </div>
  );
}
