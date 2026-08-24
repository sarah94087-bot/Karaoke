/**
 * Reading a server-sent event stream with `fetch`, because `EventSource` cannot
 * carry a token.
 *
 * T-3.10 found this live and it is worth stating plainly: since T-3.7 the
 * events endpoint requires `Authorization`, `EventSource` has no way to set a
 * header, and the session cookie belongs to the web app's domain rather than
 * the API's - so the browser's own SSE client is refused before the stream
 * opens. The progress screen sat on "reconnecting" for entire jobs and nobody
 * noticed, because T-1.11's polling fallback quietly did the work. D-18 was
 * dead in every deployment that has accounts.
 *
 * The other obvious fix is the token in a query string. This project has been
 * careful never to do that - a URL is logged by every proxy it passes, kept in
 * history, and handed on in a `Referer` - so the stream is read by hand
 * instead. It is not much: the format is `event:` and `data:` lines, a blank
 * line ends a message, and a line starting with a colon is a comment (which is
 * how the heartbeat is sent).
 *
 * What is lost is `EventSource`'s automatic reconnection, and that loss is
 * covered rather than mourned: the caller falls back to polling, which had to
 * exist anyway for the proxy that breaks streaming altogether.
 */

export interface SseMessage {
  event: string;
  data: string;
}

/**
 * Split what has arrived so far into whole messages and the remainder.
 *
 * A chunk from the network is not a message: it can hold several, or half of
 * one. The leftover is handed back so the next chunk can continue it.
 */
export function parseFrames(buffer: string): { messages: SseMessage[]; rest: string } {
  // Normalise the line endings the spec allows before splitting, so a server
  // that sends \r\n is not a different code path.
  const normalised = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const parts = normalised.split("\n\n");
  // The last piece has no blank line after it yet, so it is not finished.
  const rest = parts.pop() ?? "";
  const messages: SseMessage[] = [];

  for (const part of parts) {
    let event = "message";
    const data: string[] = [];
    for (const line of part.split("\n")) {
      // A comment. The heartbeat is one of these, and its whole job is to keep
      // the connection warm without the client seeing anything.
      if (line.startsWith(":") || line === "") continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      // One optional space after the colon belongs to the format, not the value.
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
      // `retry:` and `id:` are for EventSource's own reconnection, which this
      // reader does not do - the caller falls back to polling instead.
    }
    if (data.length > 0) messages.push({ event, data: data.join("\n") });
  }

  return { messages, rest };
}

/**
 * Open the stream and call `onMessage` for every message until it ends.
 *
 * Resolves when the server closes the stream - which it does once the job is
 * finished - and rejects if it cannot be opened or dies mid-way, which is the
 * caller's cue to start polling.
 */
export async function readEventStream(
  url: string,
  {
    token,
    signal,
    onMessage,
  }: { token?: string | null; signal?: AbortSignal; onMessage: (message: SseMessage) => void },
): Promise<void> {
  const response = await fetch(url, {
    headers: {
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    signal,
    cache: "no-store",
  });

  if (!response.ok || response.body === null) {
    throw new Error(`the event stream answered ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { messages, rest } = parseFrames(buffer);
      buffer = rest;
      for (const message of messages) onMessage(message);
    }
  } finally {
    // An abort during `read()` throws, and this still has to happen: a stream
    // left open holds a connection on a service that has exactly one instance.
    reader.cancel().catch(() => {
      /* already gone */
    });
  }
}
