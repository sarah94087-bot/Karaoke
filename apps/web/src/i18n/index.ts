/**
 * Loading a dictionary.
 *
 * A plain dynamic import of a JSON file rather than an i18n library. There is
 * one screen so far and the requirement is that the structure exists; a library
 * can replace this without touching a component, because components only ever
 * see a `Dictionary`.
 *
 * The typing is deliberate: `Dictionary` is derived from the Hebrew file, so a
 * key added to Hebrew and forgotten in English is a type error rather than a
 * blank space on a screen nobody is looking at.
 */

import he from "./dictionaries/he.json";
import type { Locale } from "./config";

export type Dictionary = typeof he;

const dictionaries: Record<Locale, () => Promise<Dictionary>> = {
  he: async () => he,
  en: async () => (await import("./dictionaries/en.json")).default as Dictionary,
};

export async function getDictionary(locale: Locale): Promise<Dictionary> {
  return dictionaries[locale]();
}
