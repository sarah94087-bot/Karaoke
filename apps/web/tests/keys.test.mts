/**
 * The keyboard shortcuts (T-5.1).
 *
 * Two things here would fail silently in a way nobody would think to look for,
 * which is why they are tests and not a careful reading:
 *
 *  - the table is matched on the *physical* key, so it works on the Hebrew
 *    layout the app is built for;
 *  - forward is the direction the words are read in, so the arrows and the
 *    scrubber (which the browser reverses in RTL) agree about which way time
 *    goes.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  SEEK_SECONDS,
  TEMPO_STEP,
  actionFor,
  isTyping,
  physicalCode,
  seekDirection,
} from "../src/lib/player/keys.ts";

test("space plays and pauses", () => {
  assert.deepEqual(actionFor({ code: "Space" }), { type: "toggle" });
});

test("space on a focused button is left to the browser", () => {
  // Otherwise one press fires the button and the shortcut - two toggles, and
  // the net effect is that nothing happens.
  assert.equal(actionFor({ code: "Space", target: { tagName: "BUTTON" } }), null);
});

test("in Hebrew the left arrow moves forwards", () => {
  assert.deepEqual(actionFor({ code: "ArrowLeft" }, "rtl"), {
    type: "seek",
    seconds: SEEK_SECONDS,
  });
  assert.deepEqual(actionFor({ code: "ArrowRight" }, "rtl"), {
    type: "seek",
    seconds: -SEEK_SECONDS,
  });
});

test("in English it is the other way round", () => {
  assert.equal(seekDirection("ArrowRight", "ltr"), 1);
  assert.equal(seekDirection("ArrowLeft", "ltr"), -1);
});

test("the arrows step the key, up is higher", () => {
  assert.deepEqual(actionFor({ code: "ArrowUp" }), { type: "key", steps: 1 });
  assert.deepEqual(actionFor({ code: "ArrowDown" }), { type: "key", steps: -1 });
});

test("minus and equals are slower and faster", () => {
  assert.deepEqual(actionFor({ code: "Minus" }), { type: "tempo", delta: -TEMPO_STEP });
  assert.deepEqual(actionFor({ code: "Equal" }), { type: "tempo", delta: TEMPO_STEP });
});

test("the letters are physical keys, not letters", () => {
  // On a Hebrew layout `event.key` for these is ה, מ and כ. A table written
  // against the letter would work for the developer and for nobody else.
  assert.deepEqual(actionFor({ code: "KeyV" }), { type: "vocals" });
  assert.deepEqual(actionFor({ code: "KeyN" }), { type: "next" });
  assert.deepEqual(actionFor({ code: "KeyF" }), { type: "fullscreen" });
  assert.deepEqual(actionFor({ code: "KeyA" }), { type: "loopStart" });
  assert.deepEqual(actionFor({ code: "KeyB" }), { type: "loopEnd" });
  assert.deepEqual(actionFor({ code: "KeyC" }), { type: "loopClear" });
});

test("a modifier means the browser's shortcut, not ours", () => {
  // Ctrl+F is Find and has to stay Find.
  assert.equal(actionFor({ code: "KeyF", ctrlKey: true }), null);
  assert.equal(actionFor({ code: "KeyN", metaKey: true }), null);
  assert.equal(actionFor({ code: "Space", altKey: true }), null);
});

test("nothing fires while somebody is typing", () => {
  // The lyrics editor is one text input per line, and V there is a letter.
  assert.equal(actionFor({ code: "KeyV", target: { tagName: "INPUT" } }), null);
  assert.equal(actionFor({ code: "ArrowUp", target: { tagName: "TEXTAREA" } }), null);
  assert.equal(actionFor({ code: "Space", target: { isContentEditable: true } }), null);
});

test("the scrubber keeps its own arrow keys", () => {
  // A range input is a form control: its arrows scrub, and the shortcut must
  // not seek five seconds on top of that.
  assert.equal(actionFor({ code: "ArrowLeft", target: { tagName: "INPUT" } }), null);
});

test("a key with no meaning here is left alone", () => {
  assert.equal(actionFor({ code: "KeyQ" }), null);
  assert.equal(actionFor({ code: "Tab" }), null);
});

test("an event with no physical key falls back to the letter", () => {
  // Browser automation dispatches keys with an empty `code`. Without the
  // fallback none of this could be checked in a browser at all.
  assert.equal(physicalCode({ code: "", key: "f" }), "KeyF");
  assert.equal(physicalCode({ code: "", key: " " }), "Space");
  assert.equal(physicalCode({ code: "", key: "ArrowUp" }), "ArrowUp");
  assert.deepEqual(actionFor({ code: "", key: "v" }), { type: "vocals" });
});

test("the physical key wins over the letter", () => {
  // A Hebrew layout produces `ה` on the V key; the code is what decides.
  assert.deepEqual(actionFor({ code: "KeyV", key: "ה" }), { type: "vocals" });
  assert.equal(actionFor({ code: "", key: "ה" }), null);
});

test("isTyping is about where the focus is, not what was pressed", () => {
  assert.equal(isTyping(null), false);
  assert.equal(isTyping({ tagName: "DIV" }), false);
  assert.equal(isTyping({ tagName: "select" }), true);
});
