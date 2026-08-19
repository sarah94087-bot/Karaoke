/**
 * Dictionary parity, with node --test: no test framework, no dependency.
 *
 * The failure this prevents is quiet. A key added to Hebrew and forgotten in
 * English does not crash - it renders `undefined`, or nothing at all, on a
 * screen that nobody is looking at in that language.
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// fileURLToPath, not URL.pathname: on Windows the latter yields "/C:/Users/..."
// with a leading slash, and every read then fails on a path that looks right.
const DIR = fileURLToPath(new URL("../src/i18n/dictionaries/", import.meta.url));

function load(name) {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8"));
}

function paths(value, prefix = "") {
  if (typeof value !== "object" || value === null) return [prefix];
  return Object.entries(value).flatMap(([key, child]) =>
    paths(child, prefix ? `${prefix}.${key}` : key),
  );
}

const files = readdirSync(DIR).filter((name) => name.endsWith(".json"));

test("there is more than one language, so the structure is a real one", () => {
  assert.ok(files.length >= 2, `only found ${files.join(", ")}`);
});

test("hebrew is present, since it is the default", () => {
  assert.ok(files.includes("he.json"));
});

test("every dictionary has exactly the same keys", () => {
  const reference = paths(load("he")).sort();

  for (const file of files) {
    const name = file.replace(".json", "");
    const actual = paths(load(name)).sort();

    const missing = reference.filter((key) => !actual.includes(key));
    const extra = actual.filter((key) => !reference.includes(key));

    assert.deepEqual(missing, [], `${name} is missing: ${missing.join(", ")}`);
    assert.deepEqual(extra, [], `${name} has keys Hebrew does not: ${extra.join(", ")}`);
  }
});

test("no string is left empty", () => {
  for (const file of files) {
    const name = file.replace(".json", "");
    const dictionary = load(name);

    for (const path of paths(dictionary)) {
      const value = path.split(".").reduce((node, key) => node[key], dictionary);
      assert.equal(typeof value, "string", `${name}.${path} is not a string`);
      assert.ok(value.trim().length > 0, `${name}.${path} is empty`);
    }
  }
});

test("the hebrew dictionary is actually in hebrew", () => {
  const hebrew = /[\u0590-\u05FF]/;
  const dictionary = load("he");

  // app.name may legitimately be a latin brand; everything a user reads should
  // not be English that was never translated.
  for (const path of ["home.title", "home.intro", "job.state.ready", "errors.song_too_long"]) {
    const value = path.split(".").reduce((node, key) => node[key], dictionary);
    assert.ok(hebrew.test(value), `${path} does not look like Hebrew: ${value}`);
  }
});
