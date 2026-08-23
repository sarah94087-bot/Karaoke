/**
 * The parts of accounts (T-3.6) that are decisions rather than network calls.
 *
 * The four requests themselves are Supabase's to get right and a live check is
 * the only thing that proves them. What is ours is the session in the cookie -
 * its shape, when it is refreshed, and what happens to one that has been
 * tampered with - and the mapping from Supabase's English prose to a code the
 * Hebrew dictionary knows.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  REFRESH_MARGIN_SECONDS,
  type Session,
  decodeSession,
  encodeSession,
  errorCode,
  linkPurpose,
  needsRefresh,
  sessionFromFragment,
} from "../src/lib/auth.ts";

const NOW = 1_800_000_000_000; // a fixed millisecond clock

function session(overrides: Partial<Session> = {}): Session {
  return {
    accessToken: "header.payload.signature",
    refreshToken: "refresh-token",
    expiresAt: Math.floor(NOW / 1000) + 3600,
    email: "sarah@example.com",
    userId: "00000000-0000-0000-0000-000000000001",
    ...overrides,
  };
}

// -- the cookie ---------------------------------------------------------------

test("a session survives the round trip through a cookie", () => {
  const original = session();

  assert.deepEqual(decodeSession(encodeSession(original)), original);
});

test("an email with a plus sign in it survives", () => {
  /* Cookie values cannot carry every character, and `sarah+karuki@…` is the
     address somebody uses precisely because they are signing up for things. */
  const original = session({ email: "sarah+karuki@example.com" });

  assert.equal(decodeSession(encodeSession(original))?.email, "sarah+karuki@example.com");
});

test("no cookie is nobody signed in", () => {
  assert.equal(decodeSession(undefined), null);
  assert.equal(decodeSession(""), null);
});

test("a cookie that is not ours reads as signed out", () => {
  /* Somebody else's cookie, or one edited by hand. "I cannot tell who this is"
     has exactly one safe reading. */
  assert.equal(decodeSession("not-json-at-all"), null);
  assert.equal(decodeSession(encodeURIComponent('{"accessToken":"only-half"}')), null);
});

// -- refreshing ---------------------------------------------------------------

test("a fresh session is left alone", () => {
  assert.equal(needsRefresh(session(), NOW), false);
});

test("a session close to expiry is refreshed before it is needed", () => {
  /* Not when it expires - before. A token that runs out mid-upload fails a
     request that was almost done, for a reason the user cannot see. */
  const nearly = session({ expiresAt: Math.floor(NOW / 1000) + REFRESH_MARGIN_SECONDS - 1 });

  assert.equal(needsRefresh(nearly, NOW), true);
});

test("an expired session is refreshed", () => {
  assert.equal(needsRefresh(session({ expiresAt: Math.floor(NOW / 1000) - 10 }), NOW), true);
});

// -- error codes --------------------------------------------------------------

test("an address that is already registered says so", () => {
  assert.equal(errorCode(422, { msg: "User already registered" }), "email_taken");
  assert.equal(errorCode(422, { error_code: "user_already_exists" }), "email_taken");
});

test("a wrong password is not the same as an unconfirmed address", () => {
  /* Two failures that both look like "cannot sign in" and need different
     sentences: one is "try again", the other is "go and read your email". */
  assert.equal(errorCode(400, { msg: "Invalid login credentials" }), "bad_credentials");
  assert.equal(errorCode(400, { error_code: "email_not_confirmed" }), "email_unconfirmed");
});

test("the free tier's email limit has its own code", () => {
  /* Supabase's built-in mail allows a couple of messages an hour. Somebody who
     asks for a reset link twice deserves to be told that, not "auth failed". */
  assert.equal(errorCode(429, { msg: "over_email_send_rate_limit" }), "too_many_attempts");
});

test("a short password says which problem it is", () => {
  assert.equal(
    errorCode(422, { msg: "Password should be at least 6 characters" }),
    "weak_password",
  );
});

test("an unrecognised failure still lands on something the dictionary knows", () => {
  assert.equal(errorCode(500, {}), "auth_failed");
  assert.equal(errorCode(400, { msg: "something new supabase started saying" }), "auth_failed");
});

// -- sessions that arrive in a link -------------------------------------------

/* The fragment of the link a confirmation email actually produced, shortened.
   The payload decodes to the email and subject below. */
const CONFIRM_TOKEN =
  "eyJhbGciOiJFUzI1NiJ9." +
  Buffer.from(
    JSON.stringify({ email: "sarah@example.com", sub: "cd7708d6-5899-4d0c-9de7-7b165fd089ba" }),
  ).toString("base64url") +
  ".signature";

test("a confirmation link carries a session, and it is taken", () => {
  /* Confirming an address does not just mark it confirmed - Supabase hands the
     browser a live session in the fragment. A live check found what ignoring it
     costs: the link lands on the library still signed out, with the token
     sitting in the address bar. */
  const hash =
    `#access_token=${CONFIRM_TOKEN}&expires_at=1787494558&expires_in=3600` +
    "&refresh_token=dyiz4stwxb4o&token_type=bearer&type=signup";

  const session = sessionFromFragment(hash, NOW);

  assert.equal(session?.refreshToken, "dyiz4stwxb4o");
  assert.equal(session?.expiresAt, 1787494558);
  assert.equal(session?.email, "sarah@example.com");
  assert.equal(session?.userId, "cd7708d6-5899-4d0c-9de7-7b165fd089ba");
});

test("an ordinary page load carries nothing", () => {
  assert.equal(sessionFromFragment("", NOW), null);
  assert.equal(sessionFromFragment("#section-2", NOW), null);
});

test("half a session in a link is not a session", () => {
  assert.equal(sessionFromFragment(`#access_token=${CONFIRM_TOKEN}`, NOW), null);
});

test("a link with no expiry falls back to the lifetime it was given", () => {
  const hash = `#access_token=${CONFIRM_TOKEN}&refresh_token=r&expires_in=3600`;

  assert.equal(sessionFromFragment(hash, NOW)?.expiresAt, Math.floor(NOW / 1000) + 3600);
});

test("a token whose payload cannot be read still produces a usable session", () => {
  /* The browser is not the one deciding whether a token is real - that is the
     API's job, with the project's public key. Here the claims are only "whose
     name goes in the corner". */
  const session = sessionFromFragment("#access_token=not.a.jwt&refresh_token=r&expires_in=60", NOW);

  assert.equal(session?.accessToken, "not.a.jwt");
  assert.equal(session?.email, "");
});

test("the link says what it was for", () => {
  /* The whole reason a reset stopped being a reset: a recovery link and a
     confirmation link carry the same shape of session, and only this tells
     them apart. Signing somebody in and taking them to the library when they
     asked to change their password looks exactly like success. */
  assert.equal(linkPurpose("#access_token=a&refresh_token=b&type=recovery"), "recovery");
  assert.equal(linkPurpose("#access_token=a&refresh_token=b&type=signup"), "signup");
  assert.equal(linkPurpose("#access_token=a&refresh_token=b"), null);
  assert.equal(linkPurpose(""), null);
});

test("retyping the password you already have says exactly that", () => {
  /* Supabase answers 422 `same_password`, and before a live check caught it
     this landed on the generic "that did not go through" - the least useful
     sentence to show somebody who has just typed a password twice. */
  assert.equal(
    errorCode(422, { error_code: "same_password", msg: "New password should be different from the old password." }),
    "same_password",
  );
});
