/**
 * Key and tempo, as the user reads them.
 *
 * T-1.14 asks for the value to be shown, not just applied, so the formatting is
 * part of the feature rather than decoration - and every interesting case is an
 * edge: zero, the ends of the range, and a fraction from a range input.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { KEY_RANGE, TEMPO_RANGE } from "../src/lib/player/engine.ts";
import {
  KEY_STEPS,
  canLowerKey,
  canRaiseKey,
  clampKey,
  clampTempo,
  formatKey,
  formatTempo,
  isDefaultKey,
  isDefaultTempo,
  stepKey,
} from "../src/lib/player/controls.ts";

test("there are thirteen keys, chapter 8's six each way plus the original", () => {
  assert.equal(KEY_STEPS.length, 13);
  assert.equal(KEY_STEPS[0], KEY_RANGE.min);
  assert.equal(KEY_STEPS.at(-1), KEY_RANGE.max);
});

test("the original key reads as 0, with no sign", () => {
  assert.equal(formatKey(0), "0");
});

test("a raised key carries a plus, or it reads as a setting instead of a change", () => {
  assert.equal(formatKey(2), "+2");
  assert.equal(formatKey(6), "+6");
});

test("a lowered key carries a minus sign", () => {
  assert.equal(formatKey(-3), "−3");
  assert.equal(formatKey(-6), "−6");
});

test("a key beyond the range is shown at the range's edge, not beyond it", () => {
  assert.equal(formatKey(99), "+6");
  assert.equal(formatKey(-99), "−6");
});

test("stepping stops at the ends rather than wrapping around", () => {
  assert.equal(stepKey(6, 1), 6);
  assert.equal(stepKey(-6, -1), -6);
  assert.equal(stepKey(0, 1), 1);
  assert.equal(stepKey(0, -1), -1);
});

test("the buttons know when they have nothing left to do", () => {
  assert.equal(canRaiseKey(6), false);
  assert.equal(canLowerKey(-6), false);
  assert.equal(canRaiseKey(5), true);
  assert.equal(canLowerKey(-5), true);
});

test("a key is a whole semitone, however it arrives", () => {
  assert.equal(clampKey(2.6), 3);
  assert.equal(clampKey(Number.NaN), 0);
});

test("tempo reads as a percentage", () => {
  assert.equal(formatTempo(1), "100%");
  assert.equal(formatTempo(0.5), "50%");
  assert.equal(formatTempo(1.5), "150%");
});

test("tempo is clamped to chapter 8's range", () => {
  assert.equal(clampTempo(9), TEMPO_RANGE.max);
  assert.equal(clampTempo(0), TEMPO_RANGE.min);
  assert.equal(clampTempo(Number.NaN), 1);
});

test("tempo snaps to steps a user can aim at, without float dust", () => {
  assert.equal(clampTempo(1.0234), 1.0);
  assert.equal(clampTempo(1.13), 1.15);
  assert.equal(formatTempo(1.13), "115%");
});

test("the defaults are recognisable, so a reset can be offered", () => {
  assert.equal(isDefaultKey(0), true);
  assert.equal(isDefaultKey(1), false);
  assert.equal(isDefaultTempo(1), true);
  assert.equal(isDefaultTempo(0.9), false);
});
