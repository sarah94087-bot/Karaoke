import type { NextConfig } from "next";

const config: NextConfig = {
  // Next 16 refuses to serve dev chunks to an origin it does not recognise, and
  // 127.0.0.1 is a different origin from localhost. Without this, opening the
  // app at http://127.0.0.1:3000 serves the server-rendered HTML and then
  // silently never hydrates: the words are on the screen and nothing is
  // clickable. Dev-only, and it costs nothing in production.
  allowedDevOrigins: ["127.0.0.1"],
  // The API is a separate service (chapter 10). In development it is on
  // localhost:8000; in production it is a different host entirely, so the base
  // URL is configuration rather than something baked in at build time.
  env: {
    // 127.0.0.1 rather than localhost: see the note in src/lib/api.ts - Node
    // resolves localhost to ::1 and the local API binds IPv4 only, which makes
    // every server-rendered page time out.
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000/api/v1",
  },
};

export default config;
