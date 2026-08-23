"use client";

import { useEffect, useState } from "react";

import type { Dictionary } from "@/i18n";
import { type SongDetail, getSong } from "@/lib/api";

/**
 * The song's key and tempo, which arrive *after* the player opens (T-3.5).
 *
 * D-28 opens this screen the moment the stems exist. The analysis that measures
 * key and tempo runs after that, deliberately - it reads the whole normalised
 * mix, which with the object store is a download, and nobody should wait
 * through it holding four finished stems. So these two numbers are the "rest of
 * the song" arriving under the singer, the same way the words do.
 *
 * It asks for the whole song and keeps **only these two fields**. That is not
 * tidiness: a refetch also brings freshly signed stem URLs, and handing those
 * to the player would rebuild the audio graph and stop the music mid-song.
 */
export function SongFacts({ song, t }: { song: SongDetail; t: Dictionary }) {
  const [facts, setFacts] = useState({ key: song.original_key, bpm: song.bpm });

  useEffect(() => {
    // Nothing else is coming for a song that has finished processing.
    if (song.status === "ready" || song.status === "failed") return;
    if (facts.key !== null && facts.bpm !== null) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const ask = async () => {
      try {
        const fresh = await getSong(song.id);
        if (cancelled) return;
        setFacts({ key: fresh.original_key, bpm: fresh.bpm });
        if (fresh.original_key !== null || fresh.status === "ready") return;
      } catch {
        // Not worth showing anyone: the numbers are a nicety and the song is
        // already playing. The next poll tries again.
        if (cancelled) return;
      }
      // The same slow cadence the lyrics use, for the same reason - one request
      // every five seconds on a free tier, for a screen that is busy playing.
      timer = setTimeout(() => void ask(), 5_000);
    };

    void ask();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [song.id, song.status, facts.key, facts.bpm]);

  if (facts.key === null && facts.bpm === null) return null;

  return (
    <dl className="song-facts">
      {facts.key !== null ? (
        <div>
          <dt>{t.song.originalKey}</dt>
          <dd className="fact-value">{facts.key}</dd>
        </div>
      ) : null}
      {facts.bpm !== null ? (
        <div>
          <dt>{t.song.bpm}</dt>
          <dd className="fact-value">
            {Math.round(facts.bpm)} <span className="fact-unit">{t.song.bpmUnit}</span>
          </dd>
        </div>
      ) : null}
    </dl>
  );
}
