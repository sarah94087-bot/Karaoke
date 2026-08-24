/**
 * Reporting a browser error to Sentry, by hand (D-24, T-3.12).
 *
 * `@sentry/nextjs` is the obvious answer and it is a large one: an
 * instrumentation file per runtime, a build plugin, source-map upload, and a
 * runtime package in an app that deliberately has three (T-1.9 - no Tailwind,
 * no ESLint, the dependency surface is the point). What it would buy is
 * grouping and source-mapped frames. What is actually needed for chapter 14's
 * checklist - and for the day something breaks on somebody's phone - is the
 * message, the stack as text, the page, and the release.
 *
 * The wire format is a Sentry *envelope*: three lines of JSON to a URL derived
 * from the DSN. The DSN is public by design - it is compiled into the bundle
 * and served to everyone - which is why it can only write events and cannot
 * read anything back.
 *
 * What is deliberately not sent: no user id, no email, no song titles, no
 * search text. An error report is not a place for somebody's library.
 */

const DSN = process.env.NEXT_PUBLIC_SENTRY_DSN ?? "";
const RELEASE = process.env.NEXT_PUBLIC_RELEASE ?? "";
const ENVIRONMENT = process.env.NODE_ENV === "production" ? "production" : "local";

/** The pieces of a DSN: `https://<key>@<host>/<projectId>`. */
export function parseDsn(dsn: string): { url: string; key: string } | null {
  try {
    const parsed = new URL(dsn);
    const projectId = parsed.pathname.replace(/^\//, "");
    if (!parsed.username || !projectId) return null;
    return {
      url: `${parsed.protocol}//${parsed.host}/api/${projectId}/envelope/`,
      key: parsed.username,
    };
  } catch {
    // A malformed DSN must never break the page it was added to protect.
    return null;
  }
}

function eventId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The envelope body: headers, item headers, and the event itself. */
export function buildEnvelope(
  error: { name?: string; message: string; stack?: string },
  context: { url: string; id: string; sentAt: string; release?: string; environment: string },
): string {
  const event = {
    event_id: context.id,
    timestamp: Date.parse(context.sentAt) / 1000,
    platform: "javascript",
    level: "error",
    environment: context.environment,
    ...(context.release ? { release: context.release } : {}),
    logger: "karuki.web",
    exception: {
      values: [{ type: error.name || "Error", value: error.message }],
    },
    // The stack goes in as text rather than parsed frames. Parsing them is
    // only worth it with source maps uploaded, and minified frames dressed up
    // as structure are worse than an honest string.
    extra: { stack: error.stack ?? "(no stack)" },
    request: { url: context.url },
  };

  return [
    JSON.stringify({ event_id: context.id, sent_at: context.sentAt }),
    JSON.stringify({ type: "event" }),
    JSON.stringify(event),
  ].join("\n");
}

let installed = false;

/** Send one error. Never throws, and does nothing at all without a DSN. */
export function reportError(error: unknown): void {
  const target = parseDsn(DSN);
  if (target === null) return;

  const asError =
    error instanceof Error ? error : { name: "Error", message: String(error), stack: undefined };

  const body = buildEnvelope(asError, {
    url: window.location.href,
    id: eventId(),
    sentAt: new Date().toISOString(),
    release: RELEASE || undefined,
    environment: ENVIRONMENT,
  });

  try {
    // `keepalive` so a report survives the navigation that often follows the
    // error that caused it.
    void fetch(`${target.url}?sentry_key=${encodeURIComponent(target.key)}&sentry_version=7`, {
      method: "POST",
      body,
      headers: { "Content-Type": "application/x-sentry-envelope" },
      keepalive: true,
      mode: "cors",
    }).catch(() => {
      /* reporting an error must never produce one */
    });
  } catch {
    /* same */
  }
}

/**
 * Listen for the two ways an error escapes a React app: one that nothing
 * caught, and a promise nobody handled.
 */
export function installErrorReporting(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  window.addEventListener("error", (event) => reportError(event.error ?? event.message));
  window.addEventListener("unhandledrejection", (event) => reportError(event.reason));
}
