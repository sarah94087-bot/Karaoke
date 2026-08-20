/**
 * Saving and restoring the player's settings.
 *
 * Two things worth pinning: the debounce, because "saved on every change" has
 * to mean "on every intent" rather than one request per pixel of fader drag,
 * and the round trip, because T-1.16's whole acceptance criterion is that a
 * song reopens the way you left it.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_SETTINGS,
  createSaver,
  keyOf,
  sameSettings,
  tempoOf,
  toMix,
  toSettings,
} from "../src/lib/player/persist.ts";
import { DEFAULT_MIX, setStemVolume, toggleVocals } from "../src/lib/player/mix.ts";

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// --- the round trip ---------------------------------------------------------

test("a mix survives being stored and read back", () => {
  const mix = setStemVolume(setStemVolume(DEFAULT_MIX, "vocals", 0.2), "drums", 0.6);

  const restored = toMix(toSettings(mix, 0, 1));

  assert.equal(restored.volumes.vocals, 0.2);
  assert.equal(restored.volumes.drums, 0.6);
});

test("removed vocals come back removed", () => {
  const removed = toggleVocals(DEFAULT_MIX);

  const restored = toMix(toSettings(removed, 0, 1));

  assert.equal(restored.volumes.vocals, 0);
  assert.equal(restored.vocalsRemoved, true, "the button would disagree with the fader");
});

test("a restored removed-vocals mix can still be un-removed", () => {
  const restored = toMix(toSettings(toggleVocals(DEFAULT_MIX), 0, 1));

  const back = toggleVocals(restored);

  assert.ok(back.volumes.vocals > 0, "the button became a no-op");
});

test("key and tempo survive the round trip", () => {
  const settings = toSettings(DEFAULT_MIX, -3, 0.85);

  assert.equal(keyOf(settings), -3);
  assert.equal(tempoOf(settings), 0.85);
});

test("a song with nothing stored opens at the defaults", () => {
  assert.deepEqual(toMix(null), DEFAULT_MIX);
  assert.equal(keyOf(null), 0);
  assert.equal(tempoOf(null), 1);
});

test("a stored value outside the range is clamped on the way in", () => {
  assert.equal(keyOf({ ...DEFAULT_SETTINGS, key_shift: 99 }), 6);
  assert.equal(tempoOf({ ...DEFAULT_SETTINGS, tempo_ratio: 99 }), 1.5);
  assert.equal(
    toMix({ ...DEFAULT_SETTINGS, stem_volumes: { vocals: 9 } }).volumes.vocals,
    1,
  );
});

test("a stored volume for an unknown stem is ignored", () => {
  const mix = toMix({ ...DEFAULT_SETTINGS, stem_volumes: { kazoo: 0.5 } });

  assert.deepEqual(mix.volumes, DEFAULT_MIX.volumes);
});

test("comparing settings ignores object identity", () => {
  const a = toSettings(DEFAULT_MIX, 2, 1.1);
  const b = toSettings(DEFAULT_MIX, 2, 1.1);

  assert.equal(sameSettings(a, b), true);
  assert.equal(sameSettings(a, { ...b, key_shift: 3 }), false);
});

// --- the debounce -----------------------------------------------------------

test("a burst of changes becomes one save", async () => {
  const saved: number[] = [];
  const saver = createSaver((s) => saved.push(s.key_shift), 30);

  for (const key of [1, 2, 3, 4, 5]) saver.schedule(toSettings(DEFAULT_MIX, key, 1));
  await tick(80);

  assert.deepEqual(saved, [5], "one request per change would be one per pixel of drag");
});

test("the save carries the latest value, not the first", async () => {
  const saved: number[] = [];
  const saver = createSaver((s) => saved.push(s.key_shift), 30);

  saver.schedule(toSettings(DEFAULT_MIX, 1, 1));
  await tick(10);
  saver.schedule(toSettings(DEFAULT_MIX, 4, 1));
  await tick(80);

  assert.deepEqual(saved, [4]);
});

test("flushing writes immediately, for a tab about to close", async () => {
  const saved: number[] = [];
  const saver = createSaver((s) => saved.push(s.key_shift), 5000);

  saver.schedule(toSettings(DEFAULT_MIX, 3, 1));
  saver.flush();

  assert.deepEqual(saved, [3], "the last change is lost when someone closes the tab");
});

test("flushing with nothing pending saves nothing", () => {
  let calls = 0;
  const saver = createSaver(() => calls++, 30);

  saver.flush();
  saver.flush();

  assert.equal(calls, 0);
});

test("cancelling stops a pending save, for an unmounting player", async () => {
  let calls = 0;
  const saver = createSaver(() => calls++, 20);

  saver.schedule(DEFAULT_SETTINGS);
  saver.cancel();
  await tick(60);

  assert.equal(calls, 0);
});
