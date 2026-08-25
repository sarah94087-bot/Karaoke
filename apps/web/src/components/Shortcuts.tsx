"use client";

import type { Dictionary } from "@/i18n";

/**
 * The shortcut list (T-5.1).
 *
 * Worth being on the screen rather than in a readme: this is the half of "run
 * an evening without touching the mouse" that somebody has to be told about
 * once. The keys are drawn as the *physical* key - see keys.ts for why the
 * table is matched on `event.code` and not on the letter a Hebrew layout
 * produces - which is also what is printed on the keycap.
 */

function Keys({ keys }: { keys: string[] }) {
  return (
    <span className="shortcut-keys ltr-number">
      {keys.map((key) => (
        <kbd key={key}>{key}</kbd>
      ))}
    </span>
  );
}

export function Shortcuts({ t, onClose }: { t: Dictionary; onClose: () => void }) {
  const rows: Array<{ keys: string[]; label: string }> = [
    { keys: ["Space"], label: t.player.shortcuts.playPause },
    { keys: ["←", "→"], label: t.player.shortcuts.seek },
    { keys: ["↑", "↓"], label: t.player.shortcuts.key },
    { keys: ["−", "+"], label: t.player.shortcuts.tempo },
    { keys: ["V"], label: t.player.shortcuts.vocals },
    { keys: ["N"], label: t.player.shortcuts.next },
    { keys: ["F"], label: t.player.shortcuts.fullscreen },
    { keys: ["A", "B", "C"], label: t.player.shortcuts.loop },
  ];

  return (
    <section className="shortcuts" aria-label={t.player.shortcuts.title}>
      <header>
        <h2>{t.player.shortcuts.title}</h2>
        <button type="button" onClick={onClose}>
          {t.player.shortcuts.hide}
        </button>
      </header>
      <dl>
        {rows.map((row) => (
          <div key={row.label}>
            <dt>
              <Keys keys={row.keys} />
            </dt>
            <dd>{row.label}</dd>
          </div>
        ))}
      </dl>
      <p className="hint">{t.player.shortcuts.note}</p>
    </section>
  );
}
