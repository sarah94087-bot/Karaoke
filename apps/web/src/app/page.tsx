import { redirect } from "next/navigation";

import { defaultLocale } from "@/i18n/config";

/**
 * `/` is not a page, it is a decision about which language to show.
 *
 * Hebrew, always, for now. When there is an account to read a preference from
 * (D-16) this is where that is honoured; today sending everyone to the default
 * is both correct and honest.
 */
export default function Root() {
  redirect(`/${defaultLocale}`);
}
