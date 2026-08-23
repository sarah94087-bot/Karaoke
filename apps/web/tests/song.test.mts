/**
 * The library row's decisions.
 *
 * Node 24 runs TypeScript directly by stripping types, so this needs no build
 * step and no test framework - which keeps the promise that the web app stays a
 * thin dependency surface.
 */

import assert from "node:assert/strict";
import test from "node:test";

import he from "../src/i18n/dictionaries/he.json" with { type: "json" };
import type { Dictionary } from "../src/i18n/index.ts";
import type { LibrarySong, SongJob } from "../src/lib/api.ts";
import { errorText, formatDuration, stateLabel } from "../src/lib/song.ts";

const t = he as Dictionary;

function song(overrides: Partial<LibrarySong> = {}, job: Partial<SongJob> | null = null): LibrarySong {
  return {
    id: "s",
    title: "שיר",
    artist: null,
    duration_sec: 100,
    status: "processing",
    is_playable: false,
    lyrics_status: "pending",
    created_at: "2026-08-19T00:00:00Z",
    job:
      job === null
        ? null
        : { id: "j", state: "running", current_step: null, progress: 0, error_code: null, ...job },
    ...overrides,
  };
}

test("a duration reads as minutes and padded seconds", () => {
  assert.equal(formatDuration(8), "0:08");
  assert.equal(formatDuration(70), "1:10");
  assert.equal(formatDuration(605), "10:05");
});

test("an unknown duration is nothing rather than 0:00", () => {
  assert.equal(formatDuration(null), null);
});

test("a running job is named by its step, not by the word processing", () => {
  const label = stateLabel(song({}, { state: "running", current_step: "separating" }), t);

  assert.equal(label, t.job.step.separating);
});

test("a job with no step yet falls back to its state", () => {
  assert.equal(stateLabel(song({}, { state: "queued" }), t), t.job.state.queued);
});

test("a finished job says ready", () => {
  assert.equal(stateLabel(song({}, { state: "ready" }), t), t.job.state.ready);
});

test("a song that never had a job is not shown as failed", () => {
  assert.equal(stateLabel(song({ status: "pending" }), t), t.job.state.queued);
});

test("a failure is rendered in hebrew from its code", () => {
  const text = errorText(song({}, { state: "failed", error_code: "separation_failed" }), t);

  assert.equal(text, t.errors.separation_failed);
});

test("a code with no translation still produces a sentence", () => {
  const text = errorText(song({}, { state: "failed", error_code: "code_from_the_future" }), t);

  assert.equal(text, t.errors.unknown);
  assert.notEqual(text, "code_from_the_future");
});

test("a song that has not failed has no error text", () => {
  assert.equal(errorText(song({}, { state: "running" }), t), null);
});

test("a song whose audio was removed after six months says so", () => {
  /* T-3.9. Chapter 9 keeps the row - the words somebody corrected, the key
     measured for them, the settings they left it in - and removes only the
     audio. The library still lists it, so it needs a line that explains why
     there is nothing to press. */
  const archived = song({ status: "archived", is_playable: false });

  assert.equal(stateLabel(archived, t), t.job.state.archived);
});

test("an archived song does not read as ready or as queued", () => {
  /* Both would be wrong in the way that wastes somebody's time: one invites a
     click that cannot work, the other suggests waiting for something that is
     not coming. */
  const label = stateLabel(song({ status: "archived" }), t);

  assert.notEqual(label, t.job.state.ready);
  assert.notEqual(label, t.job.state.queued);
});
