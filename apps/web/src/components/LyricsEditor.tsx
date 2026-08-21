"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import type { Dictionary } from "@/i18n";
import { ApiError, type SongLyrics, saveLyrics } from "@/lib/api";
import {
  type EditableLine,
  changedCount,
  editLine,
  isChanged,
  keepsWords,
  timecode,
  toEditable,
  toSave,
} from "@/lib/lyrics-edit";

/**
 * The lyrics editor: fixing the words (T-2.8).
 *
 * Phase 0 is unusually specific about the shape this needs. `T-0.4.3` timed a
 * real edit of a real transcript and found **64 words corrected against 32
 * flagged as low confidence** - twice as many. So an editor built around "jump
 * to the marked words" would miss half the work, and this one puts the whole
 * song on the screen, every line editable, nothing behind a click. The same
 * measurement recorded 5.9 minutes of active editing for a 2:46 song, which is
 * why there is no per-line dialog, no confirm step, and no mode to enter: the
 * friction is the feature's main cost.
 *
 * Saving creates a version and never overwrites (chapter 6), which is what
 * makes the version list underneath safe to use: going back to what the machine
 * wrote is a click, and going back from *that* is another one.
 */

export function LyricsEditor({
  songId,
  songTitle,
  lyrics,
  locale,
  t,
}: {
  songId: string;
  songTitle: string;
  lyrics: SongLyrics;
  locale: string;
  t: Dictionary;
}) {
  const router = useRouter();
  const [lines, setLines] = useState<EditableLine[]>(() => toEditable(lyrics.lines));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const changed = changedCount(lines);

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

      {lyrics.lines.length === 0 ? (
        <p className="hint">{t.editor.empty}</p>
      ) : (
        <ol className="editor-lines">
          {lines.map((line, index) => (
            <li key={index} className={isChanged(line) ? "editor-line is-changed" : "editor-line"}>
              <span className="editor-time ltr-number">{timecode(line.start_ms)}</span>
              <input
                className="editor-text"
                value={line.text}
                onChange={(event) => setLines(editLine(lines, index, event.target.value))}
                aria-label={`${t.editor.lineLabel} ${index + 1}`}
              />
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
