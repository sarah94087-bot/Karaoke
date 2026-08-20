/**
 * The two-stem fallback (T-1.17).
 *
 * The measurement itself needs Web Audio and a real device, and T-0.2.5 - the
 * test on actual phone hardware - is still blocked on not having a phone. What
 * can be pinned here is the decision made from a measurement, and the mapping
 * from four stems to two, which is where a mistake would be silent.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  BACKING_PARTS,
  CLEARLY_INCAPABLE,
  MODE_STORAGE_KEY,
  decide,
  readStoredMode,
  storeMode,
} from "../src/lib/player/capability.ts";
import {
  DEFAULT_MIX,
  backingVolume,
  channelVolumes,
  fadersFor,
  setBackingVolume,
  setStemVolume,
  toggleVocals,
} from "../src/lib/player/mix.ts";

test("a desktop-class cost keeps four stems", () => {
  // Phase 0 measured 0.47 for four stems at +6 on an 8-core desktop.
  assert.equal(decide(0.47), "four");
});

test("a machine the instrument over-reports still keeps four stems", () => {
  // T-1.17 measured 1.64 on a machine that was, at that moment, playing four
  // stems at +6 perfectly well. The threshold has to survive that.
  assert.equal(decide(1.64), "four");
});

test("a hopeless device falls back", () => {
  assert.equal(decide(CLEARLY_INCAPABLE), "two");
  assert.equal(decide(10), "two");
});

test("the threshold is far above what a calibrated instrument would use", () => {
  // Being wrong towards "two" costs every desktop user two faders silently;
  // being wrong towards "four" costs one tap. See capability.ts.
  assert.ok(CLEARLY_INCAPABLE > 1, "this would trust a benchmark that over-reports");
});

test("the backing channel is everything except the vocals", () => {
  assert.deepEqual([...BACKING_PARTS], ["drums", "bass", "other"]);
  assert.ok(!BACKING_PARTS.includes("vocals" as never), "removing vocals must still work");
});

test("two-stem mode gives the engine two channels", () => {
  const volumes = channelVolumes(DEFAULT_MIX, "two");

  assert.deepEqual(Object.keys(volumes).sort(), ["backing", "vocals"]);
});

test("four-stem mode gives the engine four", () => {
  const volumes = channelVolumes(DEFAULT_MIX, "four");

  assert.deepEqual(Object.keys(volumes).sort(), ["bass", "drums", "other", "vocals"]);
});

test("removing the vocals still works in two-stem mode", () => {
  const removed = toggleVocals(DEFAULT_MIX);

  const volumes = channelVolumes(removed, "two");

  assert.equal(volumes.vocals, 0);
  assert.equal(volumes.backing, 1, "the backing went quiet too");
});

test("the backing level is the average of the three stems underneath it", () => {
  const mix = setStemVolume(DEFAULT_MIX, "drums", 0.4);

  assert.equal(backingVolume(mix), Number(((0.4 + 1 + 1) / 3).toFixed(3)));
});

test("moving the backing fader moves all three", () => {
  const mix = setBackingVolume(DEFAULT_MIX, 0.5);

  assert.deepEqual(
    { drums: mix.volumes.drums, bass: mix.volumes.bass, other: mix.volumes.other },
    { drums: 0.5, bass: 0.5, other: 0.5 },
  );
});

test("the four stem volumes stay canonical, so a mix is portable", () => {
  const onAPhone = setBackingVolume(DEFAULT_MIX, 0.5);

  // Opened later on a laptop: four faders, all agreeing with what was done.
  assert.deepEqual(channelVolumes(onAPhone, "four"), {
    vocals: 1,
    drums: 0.5,
    bass: 0.5,
    other: 0.5,
  });
});

test("the mixer shows two faders in two-stem mode and four otherwise", () => {
  const all = ["vocals", "drums", "bass", "other"] as const;

  assert.deepEqual(fadersFor("two", all), ["vocals", "backing"]);
  assert.deepEqual(fadersFor("four", all), ["vocals", "drums", "bass", "other"]);
});


// --- remembering the choice -------------------------------------------------

function fakeStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    clear: () => data.clear(),
    key: (index: number) => [...data.keys()][index] ?? null,
    get length() {
      return data.size;
    },
  } as Storage;
}

test("a device with no stored choice has none", () => {
  assert.equal(readStoredMode(fakeStorage()), null);
});

test("a stored choice is read back", () => {
  const storage = fakeStorage();

  storeMode("two", storage);

  assert.equal(readStoredMode(storage), "two");
});

test("junk in storage is ignored rather than trusted", () => {
  assert.equal(readStoredMode(fakeStorage({ [MODE_STORAGE_KEY]: "sixteen" })), null);
});

test("storage being unavailable is not an error", () => {
  const broken = {
    getItem() {
      throw new Error("private browsing");
    },
    setItem() {
      throw new Error("private browsing");
    },
  } as unknown as Storage;

  assert.equal(readStoredMode(broken), null);
  storeMode("two", broken);
});
