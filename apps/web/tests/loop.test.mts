/**
 * The A–B loop's rules (T-5.2).
 *
 * The looping itself is watched on the audio clock in the component, which
 * needs Web Audio and a real song. What is decided here is everything with an
 * edge in it: which marks make a loop, what happens to a stale end when the
 * start moves, and the one that matters for not fighting the user - a loop
 * wraps on the *crossing*, not on being past the end.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  MIN_LOOP_SECONDS,
  NO_LOOP,
  clearLoop,
  crossedEnd,
  isLooping,
  loopBand,
  markEnd,
  markStart,
  wrapTo,
} from "../src/lib/player/loop.ts";

const DURATION = 240;

test("one mark is a plan, not a loop", () => {
  assert.equal(isLooping(markStart(NO_LOOP, 30, DURATION)), false);
  assert.equal(isLooping(NO_LOOP), false);
});

test("two marks make a loop", () => {
  const loop = markEnd(markStart(NO_LOOP, 30, DURATION), 45, DURATION);

  assert.deepEqual(loop, { a: 30, b: 45 });
  assert.equal(isLooping(loop), true);
});

test("marking the end first means from the top", () => {
  /* Somebody who hits "end" first has heard the phrase finish. Refusing that
     would be pedantry; the song so far is the obvious section. */
  const loop = markEnd(NO_LOOP, 20, DURATION);

  assert.deepEqual(loop, { a: 0, b: 20 });
});

test("marks given backwards are put in order", () => {
  /* Not a mistake to refuse - the two edges arrived in the order they were
     heard. */
  const loop = markEnd(markStart(NO_LOOP, 60, DURATION), 40, DURATION);

  assert.deepEqual(loop, { a: 40, b: 60 });
});

test("moving the start forward drops an end left behind it", () => {
  /* The alternative is looping backwards over music the singer has just left,
     which is the opposite of what moving the start means. */
  const loop = markStart({ a: 10, b: 20 }, 30, DURATION);

  assert.deepEqual(loop, { a: 30, b: null });
});

test("moving the start keeps an end that is still ahead", () => {
  const loop = markStart({ a: 10, b: 60 }, 30, DURATION);

  assert.deepEqual(loop, { a: 30, b: 60 });
});

test("a loop is never shorter than a phrase", () => {
  /* Two taps in a row on the two buttons. Under a second it is a buzz, and
     with the vocoder running it is where the artefacts live. */
  const loop = markEnd(markStart(NO_LOOP, 30, DURATION), 30.2, DURATION);

  assert.ok(loop.b! - loop.a! >= MIN_LOOP_SECONDS);
  assert.equal(isLooping(loop), true);
});

test("a section at the very end of the song stays inside it", () => {
  const loop = markEnd(markStart(NO_LOOP, DURATION, DURATION), DURATION, DURATION);

  assert.ok(loop.b! <= DURATION);
  assert.ok(loop.a! >= 0);
  assert.equal(isLooping(loop), true);
});

test("marks outside the song are clamped to it", () => {
  const loop = markEnd(markStart(NO_LOOP, -5, DURATION), 999, DURATION);

  assert.deepEqual(loop, { a: 0, b: DURATION });
});

test("clearing gives back a song with no section", () => {
  assert.equal(isLooping(clearLoop()), false);
});

// -- wrapping ---------------------------------------------------------------

test("playing into the end wraps to the start", () => {
  const loop = { a: 30, b: 45 };

  assert.equal(crossedEnd(loop, 44.9, 45.1), true);
  assert.equal(wrapTo(loop), 30);
});

test("a frame inside the section does not wrap", () => {
  assert.equal(crossedEnd({ a: 30, b: 45 }, 40.0, 40.1), false);
});

test("scrubbing past the end does not yank the singer back", () => {
  /* The whole reason this is a crossing and not "position >= b". Someone who
     drags the scrubber beyond the section has left it on purpose, and a loop
     that pulled them back would be fighting them. */
  assert.equal(crossedEnd({ a: 30, b: 45 }, 60, 60.1), false);
});

test("half a loop wraps nothing", () => {
  assert.equal(crossedEnd({ a: 30, b: null }, 44, 46), false);
  assert.equal(crossedEnd(NO_LOOP, 1, 2), false);
});

// -- drawing ----------------------------------------------------------------

test("the section is a band over the scrubber", () => {
  const band = loopBand({ a: 60, b: 120 }, DURATION);

  assert.deepEqual(band, { from: 25, to: 50 });
});

test("there is nothing to draw without both marks", () => {
  assert.equal(loopBand({ a: 60, b: null }, DURATION), null);
  assert.equal(loopBand({ a: 60, b: 120 }, 0), null);
});
