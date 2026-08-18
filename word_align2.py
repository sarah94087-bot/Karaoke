"""T-0.5.2 (revised) — does word-level timing track the audio inside a phrase?

The first attempt had two flaws, both corrected here:

 1. The control points for the boundary-energy test were displaced by up to 0.6s
    and could land in the silence between phrases, so the control energy was
    biased low and the "signal" was an artefact of that bias. Controls are now
    drawn from inside the same phrase.

 2. Phrase detection over-fragmented on reverb tails (128 phrases for 58 words in
    one song). The merge gap is now wider and phrases must be long enough to hold
    at least one word.

The decisive test is E. A line-level offset is something the product can fix
(T-2.7 exposes exactly that control), so the question is not whether word times
sit at the right absolute moment, but whether - once the phrase offset is
removed - the words still land on the syllable attacks in the audio. If they do,
word-level highlighting is usable with a per-song nudge. If they do not, word
timings carry no information beyond the line they belong to.
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
STEM_DIR, OUT_DIR = "output/htdemucs", "transcripts"
FRAME_S = 0.01
MIN_PHRASE_S = 0.40
MERGE_GAP_S = 0.50
RNG = np.random.default_rng(11)


def read_mono(path):
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    return a, sr


def envelope(x, sr):
    hop = int(sr * FRAME_S)
    nf = len(x) // hop
    return np.sqrt((x[:nf * hop].reshape(nf, hop) ** 2).mean(axis=1))


def phrases(env):
    floor = max(np.percentile(env, 20) * 4, env.max() * 0.02)
    voiced = env > floor
    d = np.diff(voiced.astype(np.int8))
    st = list(np.where(d == 1)[0] + 1)
    en = list(np.where(d == -1)[0] + 1)
    if voiced[0]:
        st = [0] + st
    if voiced[-1]:
        en = en + [len(env)]
    merged = []
    for s, e in zip(st, en):
        if merged and (s - merged[-1][1]) * FRAME_S < MERGE_GAP_S:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(s * FRAME_S, e * FRAME_S) for s, e in merged
            if (e - s) * FRAME_S >= MIN_PHRASE_S]


def attacks(env):
    """Times where energy rises sharply - syllable/word attacks."""
    d = np.diff(env, prepend=env[0])
    d[d < 0] = 0
    if d.max() <= 0:
        return np.array([])
    thr = np.percentile(d[d > 0], 70)
    idx = [i for i in range(1, len(d) - 1)
           if d[i] > thr and d[i] >= d[i - 1] and d[i] >= d[i + 1]]
    out = []
    for i in idx:                     # thin out attacks closer than 80 ms
        if not out or (i - out[-1]) * FRAME_S > 0.08:
            out.append(i)
    return np.array(out) * FRAME_S


summary = []

for song in SONGS:
    jpath = os.path.join(OUT_DIR, "%s.vocals.json" % song)
    wpath = os.path.join(STEM_DIR, song, "vocals.wav")
    if not (os.path.isfile(jpath) and os.path.isfile(wpath)):
        continue

    data = json.load(open(jpath, encoding="utf-8"))
    x, sr = read_mono(wpath)
    env = envelope(x, sr)
    ph = phrases(env)
    att = attacks(env)
    words = [w for s in data["segments"] for w in (s.get("words") or [])
             if w["end"] > w["start"]]
    if not words or not ph or not len(att):
        continue

    peak = env.max()

    # ---------- E: per-phrase offset removed, then word-to-attack error ----------
    raw_err, adj_err, used_phrases, offsets = [], [], 0, []
    for p0, p1 in ph:
        inside = [w for w in words if p0 - 0.3 <= w["start"] <= p1 + 0.1]
        if len(inside) < 2:
            continue
        starts = np.array([w["start"] for w in inside])
        local = att[(att >= p0 - 0.3) & (att <= p1 + 0.3)]
        if len(local) < 2:
            continue
        used_phrases += 1

        # best single shift for this phrase, found by scanning
        cands = np.arange(-0.8, 0.8001, 0.01)
        best, best_cost = 0.0, None
        for c in cands:
            cost = np.median([np.min(np.abs(local - (s + c))) for s in starts])
            if best_cost is None or cost < best_cost:
                best, best_cost = c, cost
        offsets.append(best)

        for s in starts:
            raw_err.append(np.min(np.abs(local - s)))
            adj_err.append(np.min(np.abs(local - (s + best))))

    raw_err = np.array(raw_err) if raw_err else np.array([np.nan])
    adj_err = np.array(adj_err) if adj_err else np.array([np.nan])

    # control: random word starts inside the same phrases, same count
    ctrl_err = []
    for p0, p1 in ph:
        inside = [w for w in words if p0 - 0.3 <= w["start"] <= p1 + 0.1]
        local = att[(att >= p0 - 0.3) & (att <= p1 + 0.3)]
        if len(inside) < 2 or len(local) < 2:
            continue
        rnd = RNG.uniform(p0, p1, size=len(inside))
        for s in rnd:
            ctrl_err.append(np.min(np.abs(local - s)))
    ctrl_err = np.array(ctrl_err) if ctrl_err else np.array([np.nan])

    # ---------- D (fixed): boundary energy vs within-phrase control ----------
    bounds, ctrl_pts = [], []
    for p0, p1 in ph:
        b = [w["start"] for w in words[1:] if p0 < w["start"] < p1]
        bounds += b
        if b:
            ctrl_pts += list(RNG.uniform(p0, p1, size=len(b)))
    ei = lambda t: env[int(round(t / FRAME_S))] / peak \
        if 0 <= int(round(t / FRAME_S)) < len(env) else np.nan
    e_b = np.array([ei(t) for t in bounds]) if bounds else np.array([np.nan])
    e_c = np.array([ei(t) for t in ctrl_pts]) if ctrl_pts else np.array([np.nan])

    res = {
        "song": song, "words": len(words), "phrases": len(ph),
        "attacks": int(len(att)), "phrases_used": used_phrases,
        "E_raw_median_ms": float(np.nanmedian(raw_err)) * 1000,
        "E_adj_median_ms": float(np.nanmedian(adj_err)) * 1000,
        "E_ctrl_median_ms": float(np.nanmedian(ctrl_err)) * 1000,
        "E_adj_within_100ms": float(np.nanmean(adj_err <= 0.1)) * 100,
        "E_ctrl_within_100ms": float(np.nanmean(ctrl_err <= 0.1)) * 100,
        "phrase_offset_spread_ms": float(np.std(offsets)) * 1000 if offsets else 0,
        "D_boundary_energy": float(np.nanmedian(e_b)),
        "D_control_energy": float(np.nanmedian(e_c)),
    }
    summary.append(res)

    print("=" * 66)
    print(song)
    print("=" * 66)
    print("  %d מילים · %d משפטים (%d שימשו) · %d אטאקים"
          % (res["words"], res["phrases"], res["phrases_used"], res["attacks"]))
    print("  E · שגיאה למול אטאק — גולמי %.0f ms → אחרי תיקון היסט %.0f ms"
          % (res["E_raw_median_ms"], res["E_adj_median_ms"]))
    print("      ביקורת (מילים אקראיות באותם משפטים): %.0f ms"
          % res["E_ctrl_median_ms"])
    print("      בתוך 100ms — אמיתי %.0f%% · ביקורת %.0f%%"
          % (res["E_adj_within_100ms"], res["E_ctrl_within_100ms"]))
    print("  פיזור ההיסט בין משפטים: %.0f ms" % res["phrase_offset_spread_ms"])
    print("  D · אנרגיה בגבול %.3f מול ביקורת באותו משפט %.3f"
          % (res["D_boundary_energy"], res["D_control_energy"]))
    print()

json.dump(summary, open(os.path.join(OUT_DIR, "word_align_report.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)

if summary:
    adj = np.mean([r["E_adj_median_ms"] for r in summary])
    ctrl = np.mean([r["E_ctrl_median_ms"] for r in summary])
    hit = np.mean([r["E_adj_within_100ms"] for r in summary])
    chit = np.mean([r["E_ctrl_within_100ms"] for r in summary])
    spread = np.mean([r["phrase_offset_spread_ms"] for r in summary])
    print("=" * 66)
    print("  ממוצע · שגיאה אחרי תיקון היסט: %.0f ms   ביקורת: %.0f ms" % (adj, ctrl))
    print("  ממוצע · בתוך 100ms: %.0f%%   ביקורת: %.0f%%" % (hit, chit))
    print("  ממוצע · פיזור ההיסט בין משפטים: %.0f ms" % spread)
    print()
    better = ctrl - adj
    if better > 40 and hit > chit + 15 and adj < 150:
        v = "עובד"
    elif better > 20 and hit > chit + 8:
        v = "עובד חלקית"
    else:
        v = "לא עובד"
    print("  יתרון על פני אקראי: %.0f ms טוב יותר, %+.0f נק' אחוז בתוך 100ms"
          % (better, hit - chit))
    print("  פסק דין: %s" % v)
