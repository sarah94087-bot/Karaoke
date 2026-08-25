import type { MetadataRoute } from "next";

import { getDictionary } from "@/i18n";
import { defaultLocale, directionOf } from "@/i18n/config";

/**
 * The web app manifest (T-5.3): what a browser reads before offering to
 * install this to a home screen.
 *
 * Hebrew, because there is one manifest for the whole app and Hebrew is the
 * default rather than a mode (D-20). The name comes from the dictionary rather
 * than being typed again here - two places holding the app's own name is
 * exactly the kind of pair that drifts.
 *
 * `start_url` is `/he` and not `/`: the root only redirects there, and a home
 * screen icon that spends a redirect on every launch is the opposite of the
 * "opens fast" this task is judged on.
 */

export default async function manifest(): Promise<MetadataRoute.Manifest> {
  const t = await getDictionary(defaultLocale);

  return {
    name: t.app.name,
    short_name: t.app.name,
    description: t.app.tagline,
    lang: defaultLocale,
    dir: directionOf(defaultLocale),
    start_url: `/${defaultLocale}`,
    // Standalone, not fullscreen: the player has its own full screen (T-5.1)
    // and a window with no way back to the library is a worse default.
    display: "standalone",
    // The same two values as `globals.css`. The background is what a phone
    // paints while the app is starting, so a different one would flash.
    background_color: "#10101a",
    theme_color: "#10101a",
    categories: ["music", "entertainment"],
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      // Android crops an icon to the launcher's shape. Without a maskable one
      // it pastes the square on a white plate, which on a dark icon looks
      // like a mistake.
      {
        src: "/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    // Long-press on the installed icon. Adding a song is the one thing worth
    // reaching without going through the library first.
    shortcuts: [
      {
        name: t.nav.upload,
        url: `/${defaultLocale}/upload`,
        icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }],
      },
    ],
  };
}
