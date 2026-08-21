"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Dictionary } from "@/i18n";
import { ApiError, type SongDetail, type SongLyrics, saveLyrics, stemUrl } from "@/lib/api";
import {
  type EditableLine,
  changedCount,
  editLine,
  fromPaste,
  isChanged,
  isMoved,
  keepsWords,
  nextUntimed,
  timecode,
  toEditable,
  toSave,
} from "@/lib/lyrics-edit";
import { NUDGE_MS, loopEnd, nudge, playFrom, setStart } from "@/lib/lyrics-timing";
import { PlayerEngine } from "@/lib/player/engine";

/**
 * The lyrics editor: the words (T-2.8) and their times (T-2.9).
 *
 * Phase 0 is unusually specific about the shape both halves need.
 *
 * **The words.** `T-0.4.3` timed a real edit of a real transcript and found
 * **64 words corrected against 32 flagged as low confidence** - twice as many.
 * So an editor built around "jump to the marked words" would miss half the
 * work, and this one puts the whole song on the screen, every line editable,
 * nothing behind a click. The same measurement recorded 5.9 minutes of active
 * editing for a 2:46 song, which is why there is no per-line dialog, no confirm
 * step and no mode to enter: the friction is the feature's main cost.
 *
 * **The times.** `T-0.5.3` tried tapping along in real time and it failed the
 * same way twice, on two different songs: reading the words and pressing in
 * time at once is a double task, and it slid the whole take by a line. Its
 * recommendation was a rough pass and then **a correction pass, where one line
 * is looped and nudged** - so that is what these buttons are. Play a line with a
 * lead-in, loop it, and either catch the moment or step it 100ms at a time.
 * Nothing here asks anyone to be accurate while the song runs past.
 *
 * Saving creates a version and never overwrites (chapter 6), which is what makes
 * the version list at the bottom safe to use: going back to what the machine
 * wrote is a click, and going back from *that* is another one.
 */

export function LyricsEditor({
  songId,
  songTitle,
  stems,
  lyrics,
  locale,
  t,
}: {
  songId: string;
  songTitle: string;
  /** For the timing half. An empty list is a song with nothing to listen to. */
  stems: SongDetail["stems"];
  lyrics: SongLyrics;
  locale: string;
  t: Dictionary;
}) {
  const router = useRouter();
  const [lines, setLines] = useState<EditableLine[]>(() => toEditable(lyrics.lines));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [looping, setLooping] = useState(true);
  const [audio, setAudio] = useState<"off" | "loading" | "ready" | "failed">("off");
  const [pasting, setPasting] = useState(false);
  const [pasted, setPasted] = useState("");

  const engineRef = useRef<PlayerEngine | null>(null);
  // Read inside the animation frame, which must not re-subscribe on every
  // keystroke: refs are what let the loop see the current lines without the
  // effect depending on them.
  const linesRef = useRef(lines);
  linesRef.current = lines;
  const activeRef = useRef(active);
  activeRef.current = active;
  const loopingRef = useRef(looping);
  loopingRef.current = looping;

  const changed = changedCount(lines);

  /**
   * The same engine the player uses, on its own instance. The stems are what
   * make a timing pass possible at all, and loading them is the one slow thing
   * on this screen - so it happens when the screen opens rather than on the
   * first press, which is when somebody is already listening for something.
   */
  useEffect(() => {
    if (stems.length === 0) return;
    const engine = new PlayerEngine();
    engineRef.current = engine;
    setAudio("loading");
    engine
      .load(stems.map((stem) => ({ kind: stem.kind, url: stemUrl(stem) })))
      .then(() => setAudio("ready"))
      .catch(() => setAudio("failed"));
    return () => {
      engine.dispose();
      engineRef.current = null;
    };
  }, [stems]);

  /**
   * The loop, watched on the audio clock rather than on a timer - the same rule
   * the lyrics display follows, and for the same reason: a browser timer and an
   * audio clock agree for about a minute.
   */
  useEffect(() => {
    if (audio !== "ready") return;
    let frame = 0;
    const tick = () => {
      const engine = engineRef.current;
      const index = activeRef.current;
      if (engine !== null && index !== null && loopingRef.current && engine.getState().playing) {
        if (engine.positionNow() * 1000 >= loopEnd(linesRef.current, index)) {
          engine.seek(playFrom(linesRef.current[index]) / 1000);
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [audio]);

  const playLine = useCallback((index: number) => {
    const engine = engineRef.current;
    if (engine === null) return;
    setActive(index);
    engine.seek(playFrom(linesRef.current[index]) / 1000);
    void engine.play();
  }, []);

  /** "Catch the time": this line starts *now*, wherever the song has got to. */
  const catchTime = useCallback((index: number) => {
    const engine = engineRef.current;
    if (engine === null) return;
    setActive(index);
    setLines((current) => setStart(current, index, Math.round(engine.positionNow() * 1000)));
  }, []);

  /**
   * The rough pass over a pasted song (T-2.10): one button that always means
   * "the line that is being sung now is the next one without a time".
   *
   * Phase 0's tapping failure (T-0.5.3) was partly about hunting for the right
   * control while reading and listening at once. One button in one place
   * removes the hunting; the correction pass fixes what the tapping got wrong.
   */
  const catchNext = useCallback(() => {
    const engine = engineRef.current;
    if (engine === null) return;
    const at = Math.round(engine.positionNow() * 1000);
    setLines((current) => {
      const index = nextUntimed(current, activeRef.current ?? -1);
      if (index === null) return current;
      setActive(index);
      return setStart(current, index, at);
    });
  }, []);

  async function save() {
    if (busy || changed === 0) return;
    setBusy(true);
    setError(null);
    try {
      const written = await saveLyrics(songId, toSave(lines), lyrics.language);
      setLines(toEditable(written.lines));
      setSaved(true);
      // The player and the version list both read from the server, so this is
      // the cheapest way to have them agree with what was just written.
      router.refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.code : "unknown");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="editor">
      <header className="editor-header">
        <div>
          <h1>{t.editor.title}</h1>
          <p className="tagline">{songTitle}</p>
        </div>
        <Link className="button-link" href={`/${locale}/songs/${songId}`}>
          {t.editor.toPlayer}
        </Link>
      </header>

      <p className="hint">{t.editor.explain}</p>

      {stems.length > 0 ? (
        <div className="editor-transport">
          <button
            type="button"
            onClick={() => {
              const engine = engineRef.current;
              if (engine === null) return;
              if (engine.getState().playing) engine.pause();
              else void engine.play();
            }}
            disabled={audio !== "ready"}
          >
            {t.editor.playPause}
          </button>
          <label className="editor-loop">
            <input
              type="checkbox"
              checked={looping}
              onChange={(event) => setLooping(event.target.checked)}
              disabled={audio !== "ready"}
            />
            {t.editor.loop}
          </label>
          <button type="button" onClick={catchNext} disabled={audio !== "ready"}>
            {t.editor.catchNext}
          </button>
          <span className="editor-status" aria-live="polite">
            {audio === "loading"
              ? t.player.loading
              : audio === "failed"
                ? t.player.failed
                : audio === "ready"
                  ? t.editor.audioReady
                  : ""}
          </span>
        </div>
      ) : null}

      {/*
        D-08's third source of words, and the editor's own: somebody who already
        has the lyrics should not have to wait for a model to guess them. The
        lines arrive untimed on purpose - T-2.1 stores that happily and calls the
        song `missing` until times exist, which is exactly what is true - and the
        timing is then the same job the buttons above already do.
      */}
      <section className="editor-paste">
        {pasting ? (
          <>
            <textarea
              className="editor-paste-box"
              value={pasted}
              onChange={(event) => setPasted(event.target.value)}
              placeholder={t.editor.pastePlaceholder}
              rows={8}
              aria-label={t.editor.paste}
            />
            <div className="editor-actions">
              <button
                type="button"
                onClick={() => {
                  setLines(fromPaste(pasted));
                  setActive(null);
                  setPasting(false);
                  setPasted("");
                }}
                disabled={fromPaste(pasted).length === 0}
              >
                {t.editor.pasteApply}
              </button>
              <button type="button" onClick={() => setPasting(false)}>
                {t.editor.pasteCancel}
              </button>
              <span className="editor-status">{t.editor.pasteExplain}</span>
            </div>
          </>
        ) : (
          <button type="button" onClick={() => setPasting(true)}>
            {lines.length === 0 ? t.editor.pasteFirst : t.editor.paste}
          </button>
        )}
      </section>

      {lines.length === 0 ? (
        <p className="hint">{t.editor.empty}</p>
      ) : (
        <ol className="editor-lines">
          {lines.map((line, index) => (
            <li
              key={index}
              className={[
                "editor-line",
                isChanged(line) || isMoved(line) ? "is-changed" : "",
                index === active ? "is-active" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <span className="editor-time ltr-number">{timecode(line.start_ms)}</span>
              <input
                className="editor-text"
                value={line.text}
                onChange={(event) =>
                  setLines((current) => editLine(current, index, event.target.value))
                }
                aria-label={`${t.editor.lineLabel} ${index + 1}`}
              />
              {audio === "ready" ? (
                <span className="editor-line-tools">
                  <button
                    type="button"
                    onClick={() => playLine(index)}
                    aria-label={t.editor.playLine}
                    title={t.editor.playLine}
                  >
                    &#9654;
                  </button>
                  <button
                    type="button"
                    onClick={() => catchTime(index)}
                    aria-label={t.editor.catchTime}
                    title={t.editor.catchTime}
                  >
                    {t.editor.catchTime}
                  </button>
                  <button
                    type="button"
                    // The functional form, and not `nudge(lines, ...)`: two
                    // presses inside one render both read the same `lines` and
                    // the second one is lost, which is exactly what tapping a
                    // nudge button quickly does. Found in a browser, not here.
                    onClick={() => setLines((current) => nudge(current, index, -NUDGE_MS))}
                    aria-label={t.editor.earlier}
                    title={t.editor.earlier}
                    disabled={line.start_ms === null}
                  >
                    &minus;
                  </button>
                  <button
                    type="button"
                    onClick={() => setLines((current) => nudge(current, index, NUDGE_MS))}
                    aria-label={t.editor.later}
                    title={t.editor.later}
                    disabled={line.start_ms === null}
                  >
                    +
                  </button>
                </span>
              ) : null}
              {/* Said before the save rather than after it: someone fixing one
                  word is trading the word-level highlight for a correct word,
                  and they should know that while they are doing it. */}
              {line.words.length > 0 && !keepsWords(line) ? (
                <span className="editor-note">{t.editor.wordsDropped}</span>
              ) : null}
            </li>
          ))}
        </ol>
      )}

      <div className="editor-actions">
        <button type="button" onClick={() => void save()} disabled={busy || changed === 0}>
          {busy ? t.editor.saving : t.editor.save}
        </button>
        <span className="editor-status" aria-live="polite">
          {error !== null
            ? (t.errors[error as keyof typeof t.errors] ?? t.errors.unknown)
            : changed > 0
              ? `${t.editor.changed} ${changed}`
              : saved
                ? t.editor.savedNote
                : t.editor.noChanges}
        </span>
      </div>

      {/*
        Chapter 6's versions, where they are actually useful. An ASR transcript
        somebody has half-corrected is exactly the thing people want to abandon,
        and "back to what the machine wrote" is only a real offer if it is one
        click and does not destroy the correction either.
      */}
      <section className="editor-versions">
        <h2>{t.editor.versions}</h2>
        <ul>
          {lyrics.versions.map((version) => (
            <li key={version.version}>
              <Link
                href={`/${locale}/songs/${songId}/lyrics?version=${version.version}`}
                className={version.version === lyrics.version ? "is-current" : undefined}
              >
                <span className="ltr-number">#{version.version}</span>{" "}
                {t.editor.source[version.source as keyof typeof t.editor.source] ?? version.source}
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
