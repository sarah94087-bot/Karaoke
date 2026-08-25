/**
 * The keyboard, which is how an evening runs without a mouse (T-5.1).
 *
 * The acceptance criterion is literally "you can run a whole evening without
 * touching the mouse", and the reason it is worth having is not accessibility
 * theatre: the laptop is across the room next to the speakers, the singer is
 * holding a microphone, and the person who reaches over to change the key is
 * doing it between two lines. Every control on the player screen has a key
 * here, and the same keys work in full screen, where there is nothing to aim
 * at anyway.
 *
 * ## Letters are matched on `code`, never on `key`
 *
 * This app is Hebrew first (D-20), and on a Hebrew layout the V key produces
 * `ה`, the N key produces `מ` and the F key produces `כ`. A shortcut table
 * written against `event.key` would work on the developer's English layout and
 * silently do nothing for the person the app was built for - and they would
 * have no way to tell it apart from a broken feature. `event.code` is the
 * physical key, which is what the keycap says and what the on-screen help
 * lists.
 *
 * Digits and punctuation get the same treatment: `Minus` and `Equal` are the
 * two keys next to backspace on every layout, and they mean less and more.
 */

export type PlayerAction =
  | { type: "toggle" }
  | { type: "seek"; seconds: number }
  | { type: "key"; steps: number }
  | { type: "tempo"; delta: number }
  | { type: "vocals" }
  | { type: "next" }
  | { type: "fullscreen" }
  | { type: "loopStart" }
  | { type: "loopEnd" }
  | { type: "loopClear" }
  | { type: "help" };

/** One press of an arrow. Long enough to skip an instrumental bar, short
 *  enough that finding a line again is two presses rather than a hunt. */
export const SEEK_SECONDS = 5;

/** The tempo slider's own step, so the keyboard and the mouse agree. */
export const TEMPO_STEP = 0.05;

export interface KeyTarget {
  tagName?: string;
  isContentEditable?: boolean;
}

export interface KeyEventLike {
  code: string;
  /** Only ever consulted when `code` is empty - see `physicalCode`. */
  key?: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  target?: KeyTarget | null;
}

/** Where the user is typing, and the shortcuts must keep their hands off. */
const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/**
 * Where the browser already has an opinion about the space bar.
 *
 * A focused button is activated by Space by the browser itself. Handling it
 * here as well would fire the play button *and* the shortcut - two toggles,
 * one press, nothing happens, which is the most confusing possible outcome.
 * Every other key on this list does nothing native on a button, so they are
 * handled wherever the focus happens to be.
 */
const ACTIVATES_ON_SPACE = new Set(["BUTTON", "A", "SUMMARY"]);

export function isTyping(target: KeyTarget | null | undefined): boolean {
  if (!target) return false;
  if (target.isContentEditable) return true;
  return TYPING.has((target.tagName ?? "").toUpperCase());
}

/**
 * Which way is forward.
 *
 * The page is RTL by default (T-1.9), and in Hebrew the song runs from right
 * to left along with everything else: the scrubber's own arrow keys are
 * reversed by the browser for the same reason. Pressing the arrow that points
 * the way the words are read has to move forwards, or the two controls on one
 * screen disagree about which way time goes.
 */
export function seekDirection(code: string, dir: "rtl" | "ltr"): number {
  const forward = dir === "rtl" ? "ArrowLeft" : "ArrowRight";
  return code === forward ? 1 : -1;
}

/**
 * The physical key, with a fallback for events that do not have one.
 *
 * A real browser always fills `code` in for a real keypress, so on a keyboard
 * this is just `event.code`. The fallback is for synthetic events, which is
 * not a hypothetical: the automation used to check this feature in a browser
 * dispatches keys with an empty `code`, and without this the whole table would
 * have been unverifiable outside a person's hands.
 *
 * Note what the fallback cannot do: `key` on a Hebrew layout is `ה`, not `f`,
 * so it only ever recovers a Latin layout. That asymmetry is the argument for
 * `code` being the primary, not a reason to trust `key`.
 */
export function physicalCode(event: KeyEventLike): string {
  if (event.code) return event.code;
  const key = event.key ?? "";
  if (key === " ") return "Space";
  if (key === "-") return "Minus";
  if (key === "=") return "Equal";
  if (key === "/") return "Slash";
  if (/^[a-zA-Z]$/.test(key)) return `Key${key.toUpperCase()}`;
  if (key.startsWith("Arrow")) return key;
  return "";
}

/**
 * The whole shortcut table.
 *
 * Returns null for anything this screen should keep its hands off: a modifier
 * is held (Ctrl+F is Find and must stay Find), or the user is typing.
 */
export function actionFor(event: KeyEventLike, dir: "rtl" | "ltr" = "rtl"): PlayerAction | null {
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  if (isTyping(event.target)) return null;

  switch (physicalCode(event)) {
    case "Space":
      return ACTIVATES_ON_SPACE.has((event.target?.tagName ?? "").toUpperCase())
        ? null
        : { type: "toggle" };
    case "KeyK":
      return { type: "toggle" };
    case "ArrowLeft":
    case "ArrowRight":
      return { type: "seek", seconds: SEEK_SECONDS * seekDirection(physicalCode(event), dir) };
    case "ArrowUp":
      return { type: "key", steps: 1 };
    case "ArrowDown":
      return { type: "key", steps: -1 };
    case "Equal":
      return { type: "tempo", delta: TEMPO_STEP };
    case "Minus":
      return { type: "tempo", delta: -TEMPO_STEP };
    case "KeyV":
      return { type: "vocals" };
    case "KeyN":
      return { type: "next" };
    case "KeyF":
      return { type: "fullscreen" };
    case "KeyA":
      return { type: "loopStart" };
    case "KeyB":
      return { type: "loopEnd" };
    case "KeyC":
      return { type: "loopClear" };
    case "Slash":
      return { type: "help" };
    default:
      return null;
  }
}
