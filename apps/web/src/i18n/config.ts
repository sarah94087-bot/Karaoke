/**
 * Languages and direction (D-20: i18n from the first day).
 *
 * Hebrew is the default and RTL is the default with it - not a mode the app
 * switches into. The spec is explicit that RTL is the baseline, and the
 * difference is practical rather than philosophical: a layout written in
 * left/right needs fixing screen by screen later, while one written in logical
 * properties never needs fixing at all.
 */

export const locales = ["he", "en"] as const;

export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = "he";

const direction: Record<Locale, "rtl" | "ltr"> = {
  he: "rtl",
  en: "ltr",
};

export function directionOf(locale: Locale): "rtl" | "ltr" {
  return direction[locale];
}

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}
