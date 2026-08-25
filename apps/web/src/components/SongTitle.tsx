"use client";

import { useState } from "react";

import type { Dictionary } from "@/i18n";
import { ApiError, updateSongDetails } from "@/lib/api";

/**
 * The song's name and artist, and the way to correct them (T-4.2).
 *
 * Both fields are filled automatically - from the file's own tags, from what an
 * importer read off a page, and, minutes later, from the open lyrics database
 * once it has identified the song. All three are defaults, and the whole point
 * of this component is that none of them is final: a Hebrew library is full of
 * files named `01 - track.mp3` and of tags somebody typed in 2009.
 *
 * It edits in place rather than on a settings screen of its own. The name is on
 * this page already, and a person notices it is wrong *here*, while looking at
 * it - sending them somewhere else to fix it is how a field stays wrong.
 */
export function SongTitle({
  songId,
  title,
  artist,
  t,
}: {
  songId: string;
  title: string;
  artist: string | null;
  t: Dictionary;
}) {
  const [current, setCurrent] = useState({ title, artist: artist ?? "" });
  const [draft, setDraft] = useState(current);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function open() {
    setDraft(current);
    setError(null);
    setEditing(true);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (saving) return;

    // Only what changed, which is what makes the PATCH a PATCH. A save with
    // nothing in it would still stamp the song as hand-edited and switch off
    // the automatic fill for it - for a person who opened the form and thought
    // better of it.
    const changes: { title?: string; artist?: string } = {};
    if (draft.title !== current.title) changes.title = draft.title;
    if (draft.artist !== current.artist) changes.artist = draft.artist;
    if (Object.keys(changes).length === 0) {
      setEditing(false);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const saved = await updateSongDetails(songId, changes);
      // What the server made of it, not what was typed: it trims and collapses
      // whitespace, so the screen should show the stored name rather than a
      // slightly different one until the next reload.
      setCurrent({ title: saved.title, artist: saved.artist ?? "" });
      setEditing(false);
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "unknown";
      setError(t.errors[code as keyof typeof t.errors] ?? t.errors.unknown);
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <div className="song-title">
        <h1>{current.title}</h1>
        <p className="tagline">
          {current.artist || <span className="muted">{t.song.artistUnknown}</span>}{" "}
          <button type="button" className="link-button" onClick={open}>
            {t.song.editDetails}
          </button>
        </p>
      </div>
    );
  }

  return (
    <form className="song-title card" onSubmit={save}>
      <label className="field">
        <span>{t.song.titleLabel}</span>
        <input
          value={draft.title}
          autoFocus
          disabled={saving}
          onChange={(event) => setDraft({ ...draft, title: event.target.value })}
        />
      </label>

      <label className="field">
        <span>{t.song.artistLabel}</span>
        <input
          value={draft.artist}
          disabled={saving}
          onChange={(event) => setDraft({ ...draft, artist: event.target.value })}
        />
      </label>

      {error ? <p className="song-error">{error}</p> : null}

      <div className="header-actions">
        <button type="submit" disabled={saving}>
          {saving ? t.song.savingDetails : t.song.saveDetails}
        </button>
        <button type="button" className="link-button" onClick={() => setEditing(false)}>
          {t.song.cancelDetails}
        </button>
      </div>
    </form>
  );
}
