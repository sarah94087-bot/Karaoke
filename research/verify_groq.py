"""T-0.6.1 / D-27 — verify Groq's real quotas, and check it against local Whisper.

Two things happen here, and the second is the more valuable one.

Quotas: Groq returns its rate limits in response headers. Reading them off a real
call is what T-0.6.1 asks for -- "a table that replaces the estimates" -- rather
than quoting a comparison site, which is exactly how the Modal $30-vs-$1 error
got in.

Quality: local Whisper large-v3 ran at 0.25x-0.56x realtime and is the slowest
step in the whole pipeline (T-0.4.1). If Groq's hosted large-v3 returns
comparable Hebrew text far faster, D-27 is settled on evidence.
"""

import difflib
import io
import json
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# This machine runs TLS inspection (an antivirus or corporate proxy re-signs
# HTTPS traffic), so certifi's bundle rejects the chain with "self-signed
# certificate in certificate chain". The Windows certificate store already
# trusts that root, so use it instead of disabling verification.
import truststore

truststore.inject_into_ssl()

import requests
from dotenv import load_dotenv

load_dotenv()

KEY = os.getenv("GROQ_API_KEY")
URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"
SONG = "דרוש נא (1)"
AUDIO = os.path.join("output", "htdemucs", SONG, "vocals.mp3")
LOCAL = os.path.join("transcripts", "%s.vocals.json" % SONG)

if not KEY:
    print("לא נמצא GROQ_API_KEY. צרי קובץ .env עם השורה:")
    print("   GROQ_API_KEY=gsk_...")
    sys.exit(1)

print("קובץ: %s (%.1f MB)\n" % (AUDIO, os.path.getsize(AUDIO) / 1e6))

t0 = time.time()
with open(AUDIO, "rb") as f:
    r = requests.post(
        URL,
        headers={"Authorization": "Bearer %s" % KEY},
        files={"file": (os.path.basename(AUDIO), f, "audio/mpeg")},
        data={"model": MODEL, "language": "he", "response_format": "verbose_json"},
        timeout=300,
    )
elapsed = time.time() - t0

print("HTTP %d · %.1f שניות\n" % (r.status_code, elapsed))

if r.status_code != 200:
    print("שגיאה:", r.text[:500])
    sys.exit(1)

# --- quotas straight from the response headers ---
print("=" * 62)
print("מכסות כפי שהשרת מדווח")
print("=" * 62)
limits = {k: v for k, v in r.headers.items() if "ratelimit" in k.lower()}
for k in sorted(limits):
    print("  %-38s %s" % (k, limits[k]))
if not limits:
    print("  (השרת לא החזיר כותרות מכסה בקריאה הזו)")

data = r.json()
text = data.get("text", "").strip()
dur = data.get("duration") or 0
print()
print(
    "  משך האודיו: %.1fs · זמן תמלול: %.1fs · יחס: %.1f× מזמן אמת"
    % (dur, elapsed, (dur / elapsed) if elapsed else 0)
)

# --- how it compares with the local run ---
print()
print("=" * 62)
print("מול Whisper המקומי")
print("=" * 62)

norm = lambda s: [w for w in re.sub(r"[^֐-׿A-Za-z ]", " ", s).split() if w]
remote_words = norm(text)

if os.path.isfile(LOCAL):
    local = json.load(open(LOCAL, encoding="utf-8"))
    local_text = " ".join(s["text"] for s in local["segments"])
    local_words = norm(local_text)
    sm = difflib.SequenceMatcher(None, local_words, remote_words)
    same = sum(b.size for b in sm.get_matching_blocks())
    agree = 200 * same / (len(local_words) + len(remote_words))
    local_secs = local.get("processing_sec", 0)
    print("  מילים — מקומי %d · Groq %d" % (len(local_words), len(remote_words)))
    print("  הסכמה בין השניים: %.0f%%" % agree)
    print(
        "  זמן — מקומי %.0fs · Groq %.1fs  → מהיר פי %.0f"
        % (local_secs, elapsed, local_secs / elapsed if elapsed else 0)
    )
else:
    print("  (לא נמצא תמלול מקומי להשוואה)")

foreign = [w for w in remote_words if re.search(r"[A-Za-z]", w)]
print("  טוקנים לועזיים בתמלול של Groq: %d %s" % (len(foreign), foreign[:6]))

# Fewer words is not automatically better -- T-0.4.2 showed the mix transcription
# scoring high confidence precisely because it skipped the hard passages. Time
# coverage says whether Groq transcribed the whole song or only part of it.
segs = data.get("segments") or []
if segs and dur:
    covered = sum(s["end"] - s["start"] for s in segs)
    first, last = segs[0]["start"], segs[-1]["end"]
    print()
    print(
        "  כיסוי זמן — Groq: %.0f%% (%d קטעים, %.1fs עד %.1fs)"
        % (100 * covered / dur, len(segs), first, last)
    )
    if os.path.isfile(LOCAL):
        lsegs = local["segments"]
        lcov = sum(s["end"] - s["start"] for s in lsegs)
        print(
            "  כיסוי זמן — מקומי: %.0f%% (%d קטעים, %.1fs עד %.1fs)"
            % (100 * lcov / dur, len(lsegs), lsegs[0]["start"], lsegs[-1]["end"])
        )

    rep = [s["text"].strip() for s in segs]
    dupes = len(rep) - len(set(rep))
    print("  קטעים חוזרים בדיוק: %d" % dupes)

os.makedirs("transcripts", exist_ok=True)
out = os.path.join("transcripts", "%s.groq.txt" % SONG)
open(out, "w", encoding="utf-8").write(text + "\n")
json.dump(
    {
        "quotas": limits,
        "elapsed_s": round(elapsed, 2),
        "audio_s": dur,
        "model": MODEL,
        "text": text,
        "segments": data.get("segments"),
    },
    open(os.path.join("transcripts", "groq_report.json"), "w", encoding="utf-8"),
    ensure_ascii=False,
    indent=1,
)

print()
print("  -> %s" % out)
print()
print("התמלול:")
print(text[:600])
