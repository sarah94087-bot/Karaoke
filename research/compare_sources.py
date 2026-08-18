"""T-0.4.2 — compare transcribing the isolated vocal stem vs the full mix.

This tests the assumption behind D-29: that running both is worth it because
neither source wins every time.

There is no ground-truth lyric file, so "how many words are wrong" cannot be
measured directly. Instead we use signals that do not need a reference:

  * mean word confidence      - the model's own certainty
  * share of words below 0.5  - how much a human would have to inspect
  * foreign-script tokens     - certain errors in a Hebrew transcript
  * repeated segments         - the classic silence hallucination
  * word-level disagreement   - where the two sources differ, one of them is wrong

The disagreement rate is the honest headline: it is the amount of text a human
would have to arbitrate if we only trusted one source.
"""

import collections
import difflib
import glob
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OUT = "transcripts"


def load(song, source):
    p = os.path.join(OUT, "%s.%s.json" % (song, source))
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None


def stats(d):
    words = [w for s in d["segments"] for w in s["words"]]
    texts = [s["text"] for s in d["segments"]]
    rep = sum(c for t, c in collections.Counter(texts).items() if c >= 3)
    foreign = [w["w"] for w in words if re.search(r"[A-Za-z]", w["w"])]
    low = [w for w in words if w["prob"] < 0.5]
    return {
        "words": len(words),
        "mean_conf": sum(w["prob"] for w in words) / len(words) if words else 0,
        "low_pct": 100 * len(low) / len(words) if words else 0,
        "foreign": len(foreign),
        "foreign_tokens": foreign[:6],
        "repeated_segs": rep,
        "text": [w["w"] for w in words],
    }


def norm(w):
    return re.sub(r"[^֐-׿A-Za-z]", "", w)


songs = sorted(
    {os.path.basename(p).rsplit(".", 3)[0] for p in glob.glob(os.path.join(OUT, "*.vocals.json"))}
)

rows = []
for song in songs:
    dv, do = load(song, "vocals"), load(song, "original")
    if not dv or not do:
        print("skipping %s (missing %s)" % (song, "original" if dv else "vocals"))
        continue

    sv, so = stats(dv), stats(do)
    a = [norm(w) for w in sv["text"] if norm(w)]
    b = [norm(w) for w in so["text"] if norm(w)]
    sm = difflib.SequenceMatcher(None, a, b)
    same = sum(bl.size for bl in sm.get_matching_blocks())
    disagree = 100 * (1 - 2 * same / (len(a) + len(b))) if (a and b) else 0

    rows.append({"song": song, "vocals": sv, "original": so, "disagree_pct": disagree})

    print("=" * 66)
    print(song)
    print("=" * 66)
    print("                     ערוץ שירה      תערובת")
    print("  מילים                %6d      %6d" % (sv["words"], so["words"]))
    print("  ביטחון ממוצע         %6.3f      %6.3f" % (sv["mean_conf"], so["mean_conf"]))
    print("  מתחת ל-0.5           %5.0f%%      %5.0f%%" % (sv["low_pct"], so["low_pct"]))
    print("  טוקנים לועזיים       %6d      %6d" % (sv["foreign"], so["foreign"]))
    print("  קטעים חוזרים         %6d      %6d" % (sv["repeated_segs"], so["repeated_segs"]))
    print("  אי-הסכמה בין השניים: %.1f%%" % disagree)

    # Mean confidence alone rewards a model that skips the hard passages: the mix
    # run scores high precisely because it transcribes far less of the song.
    # "Usable words" = words the model is at least half sure of, which counts
    # coverage and confidence together.
    sv["usable"] = round(sv["words"] * (1 - sv["low_pct"] / 100))
    so["usable"] = round(so["words"] * (1 - so["low_pct"] / 100))
    sv["coverage"] = 100.0
    so["coverage"] = 100 * so["words"] / sv["words"] if sv["words"] else 0
    rows[-1]["winner"] = "ערוץ שירה" if sv["usable"] > so["usable"] else "תערובת"

    print("  כיסוי התערובת מול ערוץ השירה: %.0f%% מהמילים" % so["coverage"])
    print("  מילים שמישות (>=0.5)  %6d      %6d" % (sv["usable"], so["usable"]))
    print("  מנצח לפי מילים שמישות: %s" % rows[-1]["winner"])
    print()

if rows:
    print("=" * 66)
    print("סיכום")
    print("=" * 66)
    v_wins = sum(1 for r in rows if r["winner"] == "ערוץ שירה")
    avg_cov = sum(r["original"]["coverage"] for r in rows) / len(rows)
    print("  ערוץ השירה ניצח ב-%d מתוך %d שירים" % (v_wins, len(rows)))
    print("  כיסוי ממוצע של התערובת: %.0f%% מהמילים של ערוץ השירה" % avg_cov)
    print("  אי-הסכמה ממוצעת: %.1f%%" % (sum(r["disagree_pct"] for r in rows) / len(rows)))
    print()
    if v_wins == len(rows):
        print("  D-29: ערוץ השירה מנצח בכל השירים, ולא במעט.")
        print("  ההרצה על התערובת אינה מתחרה על איכות - ערכה היחיד הוא זמן:")
        print("  היא מספקת טקסט חלקי מוקדם, לפני שההפרדה הסתיימה.")
    else:
        print("  D-29: כל מקור מנצח לפעמים - ההרצה הכפולה מוצדקת גם על איכות.")
    json.dump(
        rows,
        open(os.path.join(OUT, "compare_sources.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
        default=lambda o: None,
    )
