"use client";

import { useEffect } from "react";

import { installErrorReporting } from "@/lib/monitoring";

/**
 * Renders nothing and exists to run one line on the client (T-3.12).
 *
 * It is in the layout rather than in a particular screen because the errors
 * worth hearing about are the ones nobody predicted, and those do not happen
 * on the page you were thinking of. Without a DSN it does nothing at all,
 * which is the normal state locally.
 */
export function ErrorReporting() {
  useEffect(() => {
    installErrorReporting();
  }, []);
  return null;
}
