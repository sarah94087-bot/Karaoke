import { cookies } from "next/headers";

import { SESSION_COOKIE, decodeSession } from "@/lib/auth";

/**
 * The signed-in user's token, on the server (T-3.7).
 *
 * The library and the song pages are rendered on the server, so the *server* is
 * what asks the API for this user's songs - and it can only know who that is
 * from the cookie. This is the whole reason T-3.6 put the session in a cookie
 * rather than in localStorage, which the server cannot see at all.
 *
 * Its own file because `next/headers` may only be imported from server code,
 * and `auth.ts` is shared with the browser.
 */
export async function serverToken(): Promise<string | null> {
  const store = await cookies();
  return decodeSession(store.get(SESSION_COOKIE)?.value)?.accessToken ?? null;
}
