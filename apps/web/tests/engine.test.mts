/**
 * The engine's contract, as far as it can be checked without Web Audio.
 *
 * The constructor deliberately builds nothing - `load()` does - so the ranges,
 * the clamping and the subscription can all be exercised in plain Node. What
 * cannot be checked here is the audio itself, which phase 0 measured (0 samples
 * of drift, under a cent of pitch error) and which is verified in a browser.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { KEY_RANGE, PlayerEngine, TEMPO_RANGE, extrapolate } from "../src/lib/player/engine.ts";

test("the key range is chapter 8's: six semitones each way", () => {
  assert.deepEqual({ ...KEY_RANGE }, { min: -6, max: 6 });
});

test("the tempo range is chapter 8's: 50% to 150%", () => {
  assert.deepEqual({ ...TEMPO_RANGE }, { min: 0.5, max: 1.5 });
});

test("a new engine is not ready and not playing", () => {
  const state = new PlayerEngine().getState();

  assert.equal(state.ready, false);
  assert.equal(state.playing, false);
  assert.equal(state.position, 0);
});

test("key changes are clamped to the range and snapped to semitones", () => {
  const engine = new PlayerEngine();

  engine.setKey(99);
  assert.equal(engine.getState().semitones, 6);

  engine.setKey(-99);
  assert.equal(engine.getState().semitones, -6);

  engine.setKey(2.4);
  assert.equal(engine.getState().semitones, 2, "a fractional semitone is not a key");
});

test("tempo is clamped, and unlike key it is continuous", () => {
  const engine = new PlayerEngine();

  engine.setTempo(9);
  assert.equal(engine.getState().tempo, 1.5);

  engine.setTempo(0);
  assert.equal(engine.getState().tempo, 0.5);

  engine.setTempo(1.25);
  assert.equal(engine.getState().tempo, 1.25);
});

test("volumes are clamped to 0..1 and remembered before load", () => {
  const engine = new PlayerEngine();

  engine.setVolume("vocals", 5);
  engine.setVolume("drums", -1);

  assert.equal(engine.getVolume("vocals"), 1);
  assert.equal(engine.getVolume("drums"), 0);
  assert.equal(engine.getVolume("bass"), 1, "an untouched stem is at full volume");
});

test("a subscriber is told the current state at once, not only on change", () => {
  const engine = new PlayerEngine();
  const seen: number[] = [];

  engine.subscribe((state) => seen.push(state.semitones));

  assert.deepEqual(seen, [0], "subscribing did not deliver the initial state");
});

test("unsubscribing stops the updates", () => {
  const engine = new PlayerEngine();
  let count = 0;

  const stop = engine.subscribe(() => count++);
  stop();
  engine.setKey(3);

  assert.equal(count, 1, "still receiving after unsubscribe");
});

test("seeking before anything is loaded cannot go negative", () => {
  const engine = new PlayerEngine();

  engine.seek(-30);

  assert.equal(engine.getState().position, 0);
});

/**
 * The clock between reports (T-2.6).
 *
 * The worklet speaks every ~116ms and the lyrics budget is 100ms, so the
 * position has to be carried forward between reports. It is carried by the
 * audio clock's own elapsed time - never by a browser timer - and this is the
 * arithmetic that does it.
 */
test("between reports the position advances with the audio clock", () => {
  assert.equal(extrapolate(10, 0.05, 1, 200), 10.05);
});

test("at half speed the read head moves half as far", () => {
  // `tempo` is the playback rate, so 50ms of real time is 25ms of song.
  assert.equal(extrapolate(10, 0.05, 0.5, 200), 10.025);
});

test("the estimate never runs past the end of the song", () => {
  assert.equal(extrapolate(199.9, 5, 1, 200), 200);
});

test("the estimate never goes negative", () => {
  assert.equal(extrapolate(0, -1, 1, 200), 0);
});
