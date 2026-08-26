/**
 * Accounts (T-3.6). D-16 is closed: Supabase Auth, free tier, no card.
 *
 * **No `@supabase/supabase-js`.** T-1.9 set the rule for this app - no Tailwind,
 * no ESLint, a deliberately thin dependency surface - and the same reasoning
 * that kept boto3 and httpx out of the API applies here: what is actually
 * needed is four POSTs and a token that has to be refreshed before it expires.
 * That is this file. The upgrade path if it ever stops being enough is to add
 * the library, and nothing above `signIn`/`signOut`/`session` would change.
 *
 * **The session lives in a cookie, not in localStorage**, and that is not a
 * preference. The library and the song pages are server-rendered (T-1.10), so
 * the *server* needs the token to ask the API for this user's songs - which is
 * T-3.7's whole job. A cookie is the one place both sides can read.
 *
 * The cookie is not `httpOnly`, because the browser writes it. That is a real
 * exposure to XSS and the honest fix is a Next route handler that does the
 * token exchange server-side and sets an httpOnly cookie itself; D-31 (private
 * today, ready to open) is when that becomes worth its complexity.
 */

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const SUPABASE_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

/** Read by the browser and by server components, which is the point of it. */
export const SESSION_COOKIE = "karuki_session";

/**
 * Refresh this long before the token actually expires.
 *
 * A token that expires while a 30MB upload is in flight fails the request that
 * was almost done, and the user sees the upload die for no reason they can see.
 */
export const REFRESH_MARGIN_SECONDS = 120;

export interface Session {
  accessToken: string;
  refreshToken: string;
  /** Unix seconds. */
  expiresAt: number;
  email: string;
  userId: string;
}

export class AuthError extends Error {
  /** A code the dictionary turns into Hebrew, like everything else here. */
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: { id: string; email: string | null };
}

/**
 * Supabase's error bodies name the problem in English prose; the app shows
 * Hebrew. The mapping is small and explicit rather than clever, so an
 * unrecognised failure lands on `auth_failed` and still says something true.
 */
export function errorCode(status: number, body: { error_code?: string; msg?: string }): string {
  const raw = (body.error_code ?? body.msg ?? "").toLowerCase();
  if (raw.includes("already registered") || raw.includes("user_already_exists")) {
    return "email_taken";
  }
  if (raw.includes("invalid login") || raw.includes("invalid_credentials")) {
    return "bad_credentials";
  }
  if (raw.includes("email not confirmed") || raw.includes("email_not_confirmed")) {
    return "email_unconfirmed";
  }
  if (raw.includes("password") && raw.includes("least")) return "weak_password";
  // A live check hit this one and got the generic sentence, which is the least
  // useful thing to say to somebody who has just typed a password twice.
  if (raw.includes("same_password") || raw.includes("should be different")) {
    return "same_password";
  }
  if (status === 429 || raw.includes("rate limit") || raw.includes("over_email_send_rate")) {
    return "too_many_attempts";
  }
  return "auth_failed";
}

async function call<T>(path: string, body: unknown): Promise<T> {
  if (SUPABASE_URL === "" || SUPABASE_KEY === "") {
    throw new AuthError("auth_unconfigured", "the auth service is not configured");
  }
  let response: Response;
  try {
    response = await fetch(`${SUPABASE_URL}/auth/v1/${path}`, {
      method: "POST",
      headers: { apikey: SUPABASE_KEY, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    throw new AuthError("auth_unreachable", String(cause));
  }
  const parsed = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new AuthError(errorCode(response.status, parsed), parsed.msg ?? response.statusText);
  }
  return parsed as T;
}

function toSession(token: TokenResponse, now = Date.now()): Session {
  return {
    accessToken: token.access_token,
    refreshToken: token.refresh_token,
    expiresAt: Math.floor(now / 1000) + token.expires_in,
    email: token.user.email ?? "",
    userId: token.user.id,
  };
}

// -- the cookie ---------------------------------------------------------------

export function encodeSession(session: Session): string {
  return encodeURIComponent(JSON.stringify(session));
}

export function decodeSession(raw: string | undefined): Session | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(decodeURIComponent(raw)) as Partial<Session>;
    if (!parsed.accessToken || !parsed.refreshToken || !parsed.userId) return null;
    return parsed as Session;
  } catch {
    // A cookie from an older shape, or one somebody edited. Signed out is the
    // safe reading of "I cannot tell who this is".
    return null;
  }
}

export function needsRefresh(session: Session, now = Date.now()): boolean {
  return session.expiresAt - Math.floor(now / 1000) <= REFRESH_MARGIN_SECONDS;
}

function writeCookie(session: Session | null): void {
  if (typeof document === "undefined") return;
  if (session === null) {
    document.cookie = `${SESSION_COOKIE}=; path=/; max-age=0; samesite=lax`;
    return;
  }
  // A month, which is Supabase's own refresh-token lifetime. The access token
  // inside it lives an hour and is refreshed in place.
  const secure = window.location.protocol === "https:" ? "; secure" : "";
  document.cookie =
    `${SESSION_COOKIE}=${encodeSession(session)}; path=/; max-age=${60 * 60 * 24 * 30}` +
    `; samesite=lax${secure}`;
}

export function readCookie(): Session | null {
  if (typeof document === "undefined") return null;
  const found = document.cookie
    .split("; ")
    .find((part) => part.startsWith(`${SESSION_COOKIE}=`));
  return decodeSession(found?.slice(SESSION_COOKIE.length + 1));
}

// -- sessions that arrive in a link -------------------------------------------

/**
 * The session Supabase puts in the URL fragment, if there is one.
 *
 * Confirming an email address does not just mark it confirmed - it hands the
 * browser a live session, in the fragment of whatever address the project
 * redirects to. A live check found the cost of ignoring that: somebody clicked
 * the link, landed on the library **still signed out**, and the access token
 * sat in the address bar and in their history.
 *
 * The fragment never reaches a server, which is why this is the client's job.
 */
export function sessionFromFragment(hash: string, now = Date.now()): Session | null {
  const fragment = new URLSearchParams(hash.replace(/^#/, ""));
  const accessToken = fragment.get("access_token");
  const refreshToken = fragment.get("refresh_token");
  if (!accessToken || !refreshToken) return null;

  const expiresAt = Number(fragment.get("expires_at"));
  const claims = readClaims(accessToken);
  return {
    accessToken,
    refreshToken,
    expiresAt: Number.isFinite(expiresAt) && expiresAt > 0
      ? expiresAt
      : Math.floor(now / 1000) + Number(fragment.get("expires_in") ?? 3600),
    email: claims.email,
    userId: claims.sub,
  };
}

/**
 * The two claims this app needs, read from the token without verifying it.
 *
 * Not a security decision: the browser is not the one deciding whether the
 * token is real. That is the API's job, with the project's public key, and it
 * arrives with T-3.7. Here it is only "whose name do I put in the corner".
 */
function readClaims(accessToken: string): { email: string; sub: string } {
  try {
    const payload = accessToken.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const claims = JSON.parse(json) as { email?: string; sub?: string };
    return { email: claims.email ?? "", sub: claims.sub ?? "" };
  } catch {
    return { email: "", sub: "" };
  }
}

/** What the emailed link was for. Supabase puts it in the fragment. */
export function linkPurpose(hash: string): string | null {
  return new URLSearchParams(hash.replace(/^#/, "")).get("type");
}

export interface AdoptedLink {
  session: Session;
  /** "signup", "recovery", "magiclink", … - whatever the email was. */
  purpose: string | null;
}

/**
 * Take a session out of the current URL, store it, and clean the address bar.
 *
 * Returns what was adopted, and *why* the link existed. The purpose matters:
 * a recovery link means "this person wants to set a new password", and signing
 * them in and carrying on - which is what this did at first - turns a password
 * reset into a silent sign-in. That happened in a live check, and the reason it
 * was not obvious is that it looks like success.
 *
 * Reading the purpose here rather than relying on Supabase's redirect
 * allow-list is deliberate: `redirect_to` is honoured only if the address is
 * configured in the project, and it falls back to the site root **silently**
 * when it is not. An app one console setting away from a broken password reset
 * is not one to ship.
 *
 * The URL is rewritten whatever the purpose, because a token in the address bar
 * is a password in the address bar.
 */
export function adoptSessionFromUrl(): AdoptedLink | null {
  if (typeof window === "undefined" || !window.location.hash) return null;
  const hash = window.location.hash;
  const session = sessionFromFragment(hash);
  if (session === null) return null;
  writeCookie(session);
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return { session, purpose: linkPurpose(hash) };
}

// -- what the screens call ----------------------------------------------------

/**
 * Create an account.
 *
 * The project has email confirmation on, so this does *not* sign anyone in: it
 * sends a link. The screen has to say so, because a form that appears to do
 * nothing is how people conclude an app is broken.
 */
export async function signUp(email: string, password: string): Promise<{ confirmed: boolean }> {
  const result = await call<Partial<TokenResponse> & { user?: { id: string } }>("signup", {
    email,
    password,
  });
  if (result.access_token) {
    writeCookie(toSession(result as TokenResponse));
    return { confirmed: true };
  }
  return { confirmed: false };
}

export async function signIn(email: string, password: string): Promise<Session> {
  const token = await call<TokenResponse>("token?grant_type=password", { email, password });
  const session = toSession(token);
  writeCookie(session);
  return session;
}

/**
 * Sign out.
 *
 * The cookie is cleared **first and unconditionally**: if the network call
 * fails, the person in front of the screen has still asked to be signed out,
 * and leaving them signed in because a request timed out is the wrong way to
 * fail.
 */
export async function signOut(): Promise<void> {
  const session = readCookie();
  writeCookie(null);
  if (session === null) return;
  try {
    await fetch(`${SUPABASE_URL}/auth/v1/logout`, {
      method: "POST",
      headers: {
        apikey: SUPABASE_KEY,
        Authorization: `Bearer ${session.accessToken}`,
      },
    });
  } catch {
    // The local session is already gone, which is what was asked for.
  }
}

/** Ask for a reset link. */
export async function requestPasswordReset(email: string, redirectTo: string): Promise<void> {
  await call("recover", { email, gotrue_meta_security: {}, redirect_to: redirectTo });
}

/**
 * Set a new password, using the token the reset link puts in the URL fragment.
 * Supabase signs the user in as part of this, which is what someone who has
 * just proved they can read that mailbox expects.
 */
export async function updatePassword(accessToken: string, password: string): Promise<void> {
  const response = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    method: "PUT",
    headers: {
      apikey: SUPABASE_KEY,
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ password }),
  });
  const parsed = await response.json().catch(() => ({}));
  if (!response.ok) throw new AuthError(errorCode(response.status, parsed), parsed.msg ?? "");
}

/** Exchange the refresh token for a fresh access token, and re-store it. */
export async function refresh(session: Session): Promise<Session> {
  const token = await call<TokenResponse>("token?grant_type=refresh_token", {
    refresh_token: session.refreshToken,
  });
  const fresh = toSession(token);
  writeCookie(fresh);
  return fresh;
}

export interface CurrentSession {
  session: Session | null;
  /**
   * True when this call renewed the access token.
   *
   * It matters to the caller and not only to this file: the pages here are
   * server-rendered, so a renewal means the HTML on the screen was built with
   * the *previous* token - and if that one had already expired, the server
   * asked the API with it, got a 401, and rendered the signed-out screen for
   * somebody whose session is perfectly good. Measured on the deployment: after
   * an idle hour the library said "sign in" while the account bar showed the
   * signed-in address, and one reload put it right.
   */
  refreshed: boolean;
}

/**
 * The session to use right now, refreshed if it is close to expiring.
 *
 * `session` is null when there is nobody signed in, or when the refresh token
 * has been used up - which is indistinguishable from signed out and is treated
 * as it. `refreshed` is what the caller has to act on; see above.
 */
export async function currentSession(): Promise<CurrentSession> {
  const session = readCookie();
  if (session === null) return { session: null, refreshed: false };
  if (!needsRefresh(session)) return { session, refreshed: false };
  try {
    return { session: await refresh(session), refreshed: true };
  } catch {
    writeCookie(null);
    return { session: null, refreshed: false };
  }
}
