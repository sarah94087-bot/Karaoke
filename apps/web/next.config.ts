import type { NextConfig } from "next";

const config: NextConfig = {
  // The API is a separate service (chapter 10). In development it is on
  // localhost:8000; in production it is a different host entirely, so the base
  // URL is configuration rather than something baked in at build time.
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1",
  },
};

export default config;
