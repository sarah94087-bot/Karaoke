"use client";

import { useEffect, useRef, useState } from "react";

import type { Dictionary } from "@/i18n";
import type { LyricLine } from "@/lib/api";
import { highlightAt, wordAt } from "@/lib/lyrics";
import type { PlayerEngine } from "@/lib/player/engine";

/**
 * The lyrics area (T-2.6): the current line large, the next one dimmed, and the
 * word highlighted where the timing is good enough to say which word it is.
 *
 * Two things decide the shape of this file.
 *
 * **The clock is the engine's.** Chapter 8 forbids browser timers as the source
 * of truth, and the reason is concrete: a timer and an audio clock agree for
 * about a minute and then the lyrics slide. So the position is read from
 * `engine.positionNow()`, which is the worklet's report carried forward by the
 * audio clock itself.
 *
 * **requestAnimationFrame is the paint, not the clock.** The worklet reports
 * every ~116ms, which is longer than the 100ms the acceptance criterion allows
 * on its own, so waiting for reports would miss the target before the lyrics
 * were even involved. rAF asks "where are we" once a frame and paints; it never
 * counts time. React only re-renders when the highlighted line or word actually
 * changes, which on a real song is a few dozen times, not sixty times a second.
 */

export interface LyricsProps {
  lines: LyricLine[];
  /** `pending` while the pipeline is still working (D-28), then the real one. */
  status: "pending" | "line" | "word" | "missing";
  engine: PlayerEngine | null;
  /** T-2.7 stores this per song; T-2.6 only has to honour it. */
  offsetMs: number;
  playing: boolean;
  t: Dictionary;
}

export function Lyrics({ lines, status, engine, offsetMs, playing, t }: LyricsProps) {
  const [at, setAt] = useState<{ line: number | null; next: number | null; word: number | null }>({
    line: null,
    next: lines.length > 0 ? 0 : null,
    word: null,
  });
  const current = useRef(at);
  current.current = at;

  useEffect(() => {
    if (engine === null || lines.length === 0) return;

    let frame = 0;
    const tick = () => {
      const positionMs = engine.positionNow() * 1000;
      const { current: line, next } = highlightAt(lines, positionMs, offsetMs);
      const word = line === null ? null : wordAt(lines[line], positionMs, offsetMs);

      const previous = current.current;
      if (previous.line !== line || previous.next !== next || previous.word !== word) {
        setAt({ line, next, word });
      }
      frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // `playing` is in here so the loop restarts on play: a paused player needs
    // one pass to place the highlight and then nothing more.
  }, [engine, lines, offsetMs, playing]);

  if (status === "pending") {
    // Chapter 8 asks for "a dignified in-between state" rather than an empty
    // box, because D-28 means the player opens before the words exist and this
    // is what most people will see first.
    return (
      <section className="lyrics lyrics-waiting" aria-live="polite">
        <p className="lyrics-note">{t.lyrics.pending}</p>
      </section>
    );
  }

  if (lines.length === 0) {
    return (
      <section className="lyrics lyrics-waiting">
        <p className="lyrics-note">{t.lyrics.missing}</p>
      </section>
    );
  }

  const currentLine = at.line === null ? null : lines[at.line];
  const nextLine = at.next === null ? null : lines[at.next];

  return (
    <section className="lyrics" aria-label={t.lyrics.title}>
      <p className="lyrics-current" aria-live="off">
        {currentLine === null ? (
          <span className="lyrics-rest">{" "}</span>
        ) : currentLine.words.length > 0 ? (
          currentLine.words.map((word, index) => (
            <span
              key={`${word.start_ms}-${index}`}
              className={index === at.word ? "lyric-word is-sung" : "lyric-word"}
            >
              {word.text}
            </span>
          ))
        ) : (
          currentLine.text
        )}
      </p>
      <p className="lyrics-next">{nextLine === null ? " " : nextLine.text}</p>
    </section>
  );
}
