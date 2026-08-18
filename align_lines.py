"""T-0.5.1 — build a line-level timed lyric file and check the timings honestly.

Whisper segment timestamps are a by-product of decoding, not a measurement, so
they are treated here as a hypothesis to be tested rather than as ground truth.

The isolated vocal stem gives a strong reference: with the backing removed, a
contiguous run of above-floor energy really is a sung phrase. Detecting those
phrases gives both an error measurement for Whisper's times and a better set of
line starts to ship.

Note on an earlier bug: a first version looked for the first above-threshold
frame inside a +-2s window around each Whisper start. Whenever that window
opened mid-phrase the answer was simply the window edge, and every line reported
exactly -2000 ms. Detecting whole phrases first and then matching to their
onsets removes that failure mode.
"""
import io
import json
import os
import sys
import wave

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SONGS = [
    "דרוש נא (1)",
    "אבי לרנר - ותהי שמחה",
    "שי וינר - כנפי השכינה",
]
STEM_DIR = "output/htdemucs"
OUT_DIR = "transcripts"

FRAME_S = 0.01
MIN_PHRASE_S = 0.20       # ignore blips shorter than this
MERGE_GAP_S = 0.30        # breaths shorter than this do not split a phrase
MATCH_WINDOW_S = 2.0      # how far a Whisper start may be from its real onset


def read_mono(path):
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    return a, sr


def phrases(x, sr):
    """Start/end times of contiguous sung phrases in the isolated vocal stem."""
    hop = int(sr * FRAME_S)
    nf = len(x) // hop
    env = np.sqrt((x[:nf * hop].reshape(nf, hop) ** 2).mean(axis=1))
    floor = max(np.percentile(env, 20) * 4, env.max() * 0.02)

    voiced = env > floor
    d = np.diff(voiced.astype(np.int8))
    starts = list(np.where(d == 1)[0] + 1)
    ends = list(np.where(d == -1)[0] + 1)
    if voiced[0]:
        starts = [0] + starts
    if voiced[-1]:
        ends = ends + [nf]

    regs = [(s, e) for s, e in zip(starts, ends)
            if (e - s) * FRAME_S >= MIN_PHRASE_S]
    merged = []
    for s, e in regs:
        if merged and (s - merged[-1][1]) * FRAME_S < MERGE_GAP_S:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(s * FRAME_S, e * FRAME_S) for s, e in merged]


def fmt_lrc_time(t):
    t = max(0.0, t)
    m = int(t // 60)
    return "[%02d:%05.2f]" % (m, t - m * 60)


def write_lrc(path, title, pairs):
    lines = ["[ti:%s]" % title, "[re:karuki T-0.5.1]", ""]
    lines += ["%s%s" % (fmt_lrc_time(t), txt) for t, txt in pairs]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


results = []

for song in SONGS:
    jpath = os.path.join(OUT_DIR, "%s.vocals.json" % song)
    wpath = os.path.join(STEM_DIR, song, "vocals.wav")
    if not (os.path.isfile(jpath) and os.path.isfile(wpath)):
        print("skipping %s" % song)
        continue

    data = json.load(open(jpath, encoding="utf-8"))
    x, sr = read_mono(wpath)
    ph = phrases(x, sr)
    onsets = np.array([p[0] for p in ph])

    segs = [s for s in data["segments"] if s["text"].strip()]

    deltas, snapped, rows = [], [], []
    used = set()
    for s in segs:
        near, d = None, None
        if len(onsets):
            # Never let two lines land on the same onset: an onset already claimed
            # by an earlier line is skipped, otherwise the output has duplicate
            # timestamps and no tool downstream can map lines to times one-to-one.
            order = np.argsort(np.abs(onsets - s["start"]))
            for i in order:
                if int(i) not in used:
                    near, d = float(onsets[i]), float(onsets[i] - s["start"])
                    break
        matched = near is not None and abs(d) <= MATCH_WINDOW_S
        if matched:
            deltas.append(d)
            used.add(int(np.where(onsets == near)[0][0]))
        snapped.append((near if matched else s["start"], s["text"].strip()))
        rows.append({"whisper_start": s["start"], "onset": near,
                     "delta_ms": None if d is None else round(d * 1000, 1),
                     "matched": matched, "text": s["text"].strip()})

    # keep line starts strictly increasing so the lyric file stays playable
    for i in range(1, len(snapped)):
        if snapped[i][0] <= snapped[i - 1][0]:
            snapped[i] = (snapped[i - 1][0] + 0.05, snapped[i][1])

    write_lrc(os.path.join(OUT_DIR, "%s.raw.lrc" % song), song,
              [(s["start"], s["text"].strip()) for s in segs])
    write_lrc(os.path.join(OUT_DIR, "%s.aligned.lrc" % song), song + " (aligned)",
              snapped)

    a = np.abs(deltas) if deltas else np.array([0.0])
    res = {
        "song": song,
        "whisper_segments": len(segs),
        "detected_phrases": len(ph),
        "matched": len(deltas),
        "median_offset_ms": float(np.median(deltas)) * 1000 if deltas else 0.0,
        "median_abs_ms": float(np.median(a)) * 1000,
        "mean_abs_ms": float(a.mean()) * 1000,
        "p90_abs_ms": float(np.percentile(a, 90)) * 1000,
        "max_abs_ms": float(a.max()) * 1000,
        "within_100ms_pct": 100.0 * float((a <= 0.1).mean()),
        "within_300ms_pct": 100.0 * float((a <= 0.3).mean()),
    }
    results.append(res)

    print("=" * 64)
    print(song)
    print("=" * 64)
    print("  קטעי Whisper: %d   ·   משפטים שזוהו באודיו: %d"
          % (res["whisper_segments"], res["detected_phrases"]))
    print("  הותאמו: %d" % res["matched"])
    print("  הטיה שיטתית (חציון): %+.0f ms" % res["median_offset_ms"])
    print("  שגיאה מוחלטת — חציון %.0f · ממוצע %.0f · p90 %.0f · מקס %.0f (ms)"
          % (res["median_abs_ms"], res["mean_abs_ms"],
             res["p90_abs_ms"], res["max_abs_ms"]))
    print("  בתוך 100ms: %.0f%%   ·   בתוך 300ms: %.0f%%"
          % (res["within_100ms_pct"], res["within_300ms_pct"]))
    print()

json.dump(results, open(os.path.join(OUT_DIR, "align_report.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)

if results:
    print("=" * 64)
    print("  יעד האפיון (T-2.6): סטייה מתחת ל-100ms.")
    print("  שורות שעומדות בו עם תזמוני Whisper גולמיים: %.0f%% בממוצע"
          % (sum(r["within_100ms_pct"] for r in results) / len(results)))
    print()
    print("  קטעי Whisper מול משפטים שזוהו: %d מול %d."
          % (sum(r["whisper_segments"] for r in results),
             sum(r["detected_phrases"] for r in results)))
    print("  קטע של Whisper אינו שורת קריוקי - הוא מאחד כמה משפטים מושרים.")
