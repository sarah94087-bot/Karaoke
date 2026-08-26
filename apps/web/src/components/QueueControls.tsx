"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import type { Dictionary } from "@/i18n";
import {
  EMPTY_QUEUE,
  QUEUE_EVENT,
  type Queue,
  type QueueEntry,
  contains,
  dequeue,
  enqueue,
  head,
  loadQueue,
  positionOf,
  storeQueue,
} from "@/lib/player/queue";

/**
 * The evening's running order, on the library screen (T-5.1).
 *
 * Two pieces that have to agree with each other: a button on every row, and
 * the list itself. They are in one file because they share the hook below and
 * nothing else uses it.
 */

/**
 * The queue as this screen sees it.
 *
 * It starts empty rather than reading storage during render, because the
 * library is server-rendered and a first client render that disagrees with the
 * server's HTML is a hydration error. The real queue arrives one effect later,
 * which is a frame nobody can see.
 *
 * Both events matter: `storage` is fired in *other* tabs, and the custom one is
 * how the button and the panel on this screen hear each other.
 */
export function useQueue(): [Queue, (update: (current: Queue) => Queue) => void] {
  const [queue, setQueue] = useState<Queue>(EMPTY_QUEUE);

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

  /**
   * Write against what is *stored*, not against the render's snapshot.
   *
   * A live check on the deployment found this: three "add to queue" presses in
   * one tick left one song queued. Every button holds its own copy of the
   * queue from its last render, and React had not re-rendered any of them
   * between the presses - so all three computed from the same empty list and
   * the last write won. Storage is the shared truth here, so reading it at
   * write time is both the correct fix and the cheap one. Same shape as the
   * bug T-2.9 found in the lyrics editor's nudge buttons.
   */
  const write = useCallback((update: (current: Queue) => Queue) => {
    const next = update(loadQueue());
    storeQueue(next);
    setQueue(next);
  }, []);

  return [queue, write];
}

/**
 * Add this song to tonight, or take it out again.
 *
 * On the row rather than inside the song, because the queue is built by
 * looking down the library once - and a screen you have to open to add a song
 * to a list is a screen nobody builds a list on.
 */
export function QueueButton({ song, t }: { song: QueueEntry; t: Dictionary }) {
  const [queue, write] = useQueue();
  const queued = contains(queue, song.id);

  return (
    <button
      type="button"
      className="queue-button"
      data-queued={queued}
      onClick={() =>
        write((current) =>
          contains(current, song.id) ? dequeue(current, song.id) : enqueue(current, song),
        )
      }
    >
      {queued ? t.queue.remove : t.queue.add}
      {queued ? <span className="ltr-number"> {positionOf(queue, song.id)}</span> : null}
    </button>
  );
}

/**
 * The list, with the one button that starts the evening.
 *
 * Nothing at all when the queue is empty: an empty box explaining a feature
 * nobody has used yet is noise on the screen people open most.
 */
export function QueuePanel({ locale, t }: { locale: string; t: Dictionary }) {
  const [queue, write] = useQueue();
  const first = head(queue);
  if (first === null) return null;

  return (
    <section className="queue-panel" aria-label={t.queue.title}>
      <header className="queue-head">
        <h2>{t.queue.title}</h2>
        <span className="queue-count ltr-number">{queue.entries.length}</span>
      </header>

      <ol className="queue-list">
        {queue.entries.map((entry) => (
          <li key={entry.id}>
            <Link href={`/${locale}/songs/${entry.id}`}>{entry.title}</Link>
            <button type="button" onClick={() => write((current) => dequeue(current, entry.id))}>
              {t.queue.remove}
            </button>
          </li>
        ))}
      </ol>

      <div className="queue-actions">
        {/* Autoplay is asked for in the address rather than held in storage:
            it belongs to this navigation and not to the song, so a reload of
            the player later in the evening does not restart the music on its
            own. The player strips it once it has acted on it. */}
        <Link className="button-link" href={`/${locale}/songs/${first.id}?autoplay=1`}>
          {t.queue.start}
        </Link>
        <button type="button" onClick={() => write(() => EMPTY_QUEUE)}>
          {t.queue.clear}
        </button>
      </div>
      <p className="hint">{t.queue.hint}</p>
    </section>
  );
}
