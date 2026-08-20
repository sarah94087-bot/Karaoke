/**
 * The mixer's rules.
 *
 * The one that matters: pressing "remove vocals" twice puts you back exactly
 * where you were, and the button and the fader never contradict each other.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { DEFAULT_MIX, STEM_ORDER, asPercent, setStemVolume, toggleVocals } from "../src/lib/player/mix.ts";

test("there are four faders, vocals first", () => {
  assert.deepEqual([...STEM_ORDER], ["vocals", "drums", "bass", "other"]);
});

test("everything starts at full volume and nothing is removed", () => {
  assert.deepEqual(DEFAULT_MIX.volumes, { vocals: 1, drums: 1, bass: 1, other: 1 });
  assert.equal(DEFAULT_MIX.vocalsRemoved, false);
});

test("removing the vocals silences them", () => {
  const mix = toggleVocals(DEFAULT_MIX);

  assert.equal(mix.volumes.vocals, 0);
  assert.equal(mix.vocalsRemoved, true);
});

test("removing the vocals leaves the other three alone", () => {
  const mix = toggleVocals(DEFAULT_MIX);

  assert.deepEqual(
    { drums: mix.volumes.drums, bass: mix.volumes.bass, other: mix.volumes.other },
    { drums: 1, bass: 1, other: 1 },
  );
});

test("pressing it twice puts you back where you were, not at full volume", () => {
  const guide = setStemVolume(DEFAULT_MIX, "vocals", 0.2);

  const back = toggleVocals(toggleVocals(guide));

  assert.equal(back.volumes.vocals, 0.2);
  assert.equal(back.vocalsRemoved, false);
});

test("dragging the vocals fader to zero counts as removing them", () => {
  const mix = setStemVolume(DEFAULT_MIX, "vocals", 0);

  assert.equal(mix.vocalsRemoved, true, "the button would contradict the fader");
});

test("dragging the vocals fader back up counts as bringing them back", () => {
  const mix = setStemVolume(setStemVolume(DEFAULT_MIX, "vocals", 0), "vocals", 0.5);

  assert.equal(mix.vocalsRemoved, false);
});

test("the restore level never becomes zero, or the button would do nothing", () => {
  const silenced = setStemVolume(DEFAULT_MIX, "vocals", 0);

  const restored = toggleVocals(silenced);

  assert.ok(restored.volumes.vocals > 0);
});

test("another fader reaching zero does not mark the vocals as removed", () => {
  const mix = setStemVolume(DEFAULT_MIX, "drums", 0);

  assert.equal(mix.vocalsRemoved, false);
  assert.equal(mix.volumes.drums, 0);
});

test("volumes are clamped, including from a broken range input", () => {
  assert.equal(setStemVolume(DEFAULT_MIX, "bass", 5).volumes.bass, 1);
  assert.equal(setStemVolume(DEFAULT_MIX, "bass", -1).volumes.bass, 0);
  assert.equal(setStemVolume(DEFAULT_MIX, "bass", Number.NaN).volumes.bass, 0);
});

test("state is replaced rather than mutated, so React sees the change", () => {
  const before = DEFAULT_MIX;

  const after = toggleVocals(before);

  assert.notEqual(after, before);
  assert.equal(before.volumes.vocals, 1, "the original was mutated");
});

test("percentages are whole numbers for the label", () => {
  assert.equal(asPercent(0.256), 26);
  assert.equal(asPercent(1), 100);
  assert.equal(asPercent(0), 0);
});
