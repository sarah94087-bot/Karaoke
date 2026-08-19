/**
 * The root layout exists only to satisfy Next's requirement for one.
 *
 * The real <html> element, with its lang and dir, is in [locale]/layout.tsx:
 * direction is a property of the language, so it belongs where the language is
 * known and nowhere else.
 */

import type { ReactNode } from "react";

export default function RootLayout({ children }: { children: ReactNode }) {
  return children;
}
