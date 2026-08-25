/**
 * The playback queue (T-5.1): an evening of songs, in order.
 *
 * The acceptance criterion for this task is "you can run a whole evening
 * without touching the mouse", and a queue is the half of that which survives
 * between songs: pick six songs once, and the player moves through them by
 * itself while the room sings.
 *
 * ## Why this lives on the device and not in the database
 *
 * T-5.2 made the same call about the A-B loop and for the same reason: a
 * practice section, or an evening's running order, belongs to the half hour it
 * was made in and not to the song for ever. Nothing here is worth a table, a
 * migration or a round trip.
 *
 * But unlike the loop it cannot be component state. Every song is its own
 * server-rendered page (`/he/songs/<id>`), so advancing to the next song is a
 * navigation, and anything held in React is gone by the time the next player
 * mounts. `localStorage` is what makes the queue outlive the page that built
 * it - and it also survives the tab being closed by accident half way through
 * an evening, which `sessionStorage` would not.
 *
 * ## Titles are a snapshot, on purpose
 *
 * An entry carries the title as well as the id, because the player page has to
 * name the next song and knows nothing about the library. T-4.2 lets a title
 * change, so the stored one can go stale; `refresh` puts it right whenever a
 * song is opened. A queue is a list for tonight, not a second copy of the
 * library.
 */

export interface QueueEntry {
  id: string;
  title: string;
}

export interface Queue {
  entries: QueueEntry[];
}

export const EMPTY_QUEUE: Queue = { entries: [] };

export const QUEUE_STORAGE_KEY = "karuki:queue";

/**
 * More than an evening holds. This is not a limit anyone should meet; it is
 * there so a stuck loop somewhere cannot grow a storage key without bound.
 */
export const MAX_QUEUE = 100;

/**
 * Add a song to the end.
 *
 * A song already queued is left where it is rather than moved or repeated.
 * Both alternatives are worse: repeating makes "which one is next after this
 * one" ambiguous, and moving would silently reorder an evening somebody has
 * already arranged because they pressed a button twice.
 */
export function enqueue(queue: Queue, entry: QueueEntry): Queue {
  if (contains(queue, entry.id)) return queue;
  if (queue.entries.length >= MAX_QUEUE) return queue;
  return { entries: [...queue.entries, entry] };
}

export function dequeue(queue: Queue, id: string): Queue {
  return { entries: queue.entries.filter((entry) => entry.id !== id) };
}

export function contains(queue: Queue, id: string): boolean {
  return queue.entries.some((entry) => entry.id === id);
}

/** 1-based, for showing "3 of 6". Zero when the song is not queued. */
export function positionOf(queue: Queue, id: string): number {
  return queue.entries.findIndex((entry) => entry.id === id) + 1;
}

/**
 * What plays after this song, or null.
 *
 * Null for the last song, and null for a song that is not in the queue at all -
 * opening one song from the library in the middle of an evening should not
 * drop the singer into somebody else's running order when it ends.
 */
export function nextAfter(queue: Queue, id: string): QueueEntry | null {
  const index = queue.entries.findIndex((entry) => entry.id === id);
  if (index < 0) return null;
  return queue.entries[index + 1] ?? null;
}

/** The first song of the evening, for the "start" button on the library. */
export function head(queue: Queue): QueueEntry | null {
  return queue.entries[0] ?? null;
}

/** Correct a stored title against the song that was actually opened. */
export function refresh(queue: Queue, entry: QueueEntry): Queue {
  const known = queue.entries.find((item) => item.id === entry.id);
  if (known === undefined || known.title === entry.title) return queue;
  return {
    entries: queue.entries.map((item) =>
      item.id === entry.id ? { ...item, title: entry.title } : item,
    ),
  };
}

/**
 * Anything that is not a queue reads as an empty one.
 *
 * The key is in a place the user can edit, it survives a deploy that changes
 * the shape, and there is nothing here worth throwing an exception over on a
 * screen that is about to play music.
 */
export function parseQueue(raw: string | null): Queue {
  if (raw === null) return EMPTY_QUEUE;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object") return EMPTY_QUEUE;
    const entries = (parsed as { entries?: unknown }).entries;
    if (!Array.isArray(entries)) return EMPTY_QUEUE;
    const clean: QueueEntry[] = [];
    for (const item of entries) {
      if (item === null || typeof item !== "object") continue;
      const { id, title } = item as { id?: unknown; title?: unknown };
      if (typeof id !== "string" || id === "") continue;
      if (clean.some((kept) => kept.id === id)) continue;
      clean.push({ id, title: typeof title === "string" ? title : "" });
    }
    return { entries: clean.slice(0, MAX_QUEUE) };
  } catch {
    return EMPTY_QUEUE;
  }
}

export function loadQueue(storage?: Storage): Queue {
  try {
    return parseQueue((storage ?? window.localStorage).getItem(QUEUE_STORAGE_KEY));
  } catch {
    // Private browsing, or storage disabled. An evening with no queue still
    // plays songs one at a time, which is what the app did before this task.
    return EMPTY_QUEUE;
  }
}

/**
 * Write it, and tell this tab.
 *
 * The `storage` event only fires in *other* tabs, so the queue button in the
 * library and the panel next to it would not see each other's writes. One
 * custom event on `window` is the whole subscription mechanism - there is no
 * shared state library in this app and this is not the feature that earns one.
 */
export const QUEUE_EVENT = "karuki:queue-changed";

export function storeQueue(queue: Queue, storage?: Storage): void {
  try {
    (storage ?? window.localStorage).setItem(QUEUE_STORAGE_KEY, JSON.stringify(queue));
  } catch {
    // As above.
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(QUEUE_EVENT));
  }
}
