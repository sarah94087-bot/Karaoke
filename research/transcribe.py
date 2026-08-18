"""T-0.4.1 — transcribe the isolated vocal stem of Hebrew songs.

Word timestamps are captured here even though T-0.4.1 only asks for text: T-0.4.2
and T-0.5 need them, and re-running the model costs ~15 minutes per pass.
"""

import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from faster_whisper import WhisperModel

SONGS = [
    "דרוש נא (1)",
    "אבי לרנר - ותהי שמחה",
    "שי וינר - כנפי השכינה",
]
STEM_DIR = "output/htdemucs"
OUT_DIR = "transcripts"
SOURCE = sys.argv[1] if len(sys.argv) > 1 else "vocals"  # "vocals" or "original"

os.makedirs(OUT_DIR, exist_ok=True)

print("loading model…")
t0 = time.time()
model = WhisperModel("large-v3", device="cpu", compute_type="int8")
print("model ready in %.1fs\n" % (time.time() - t0))

report = []

for song in SONGS:
    path = os.path.join(STEM_DIR, song, SOURCE + ".wav")
    if not os.path.isfile(path):
        print("!! missing %s" % path)
        continue

    print("=== %s [%s] ===" % (song, SOURCE))
    t1 = time.time()
    segments, info = model.transcribe(
        path,
        language="he",
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    segs = list(segments)
    elapsed = time.time() - t1
    audio_len = info.duration

    lines, payload = [], []
    for s in segs:
        lines.append(s.text.strip())
        payload.append(
            {
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "text": s.text.strip(),
                "avg_logprob": round(s.avg_logprob, 3),
                "no_speech_prob": round(s.no_speech_prob, 3),
                "words": [
                    {
                        "w": w.word.strip(),
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "prob": round(w.probability, 3),
                    }
                    for w in (s.words or [])
                ],
            }
        )

    base = os.path.join(OUT_DIR, "%s.%s" % (song, SOURCE))
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "song": song,
                "source": SOURCE,
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
                "duration": round(audio_len, 2),
                "processing_sec": round(elapsed, 1),
                "segments": payload,
            },
            f,
            ensure_ascii=False,
            indent=1,
        )

    n_words = sum(len(s["words"]) for s in payload)
    mean_prob = (sum(w["prob"] for s in payload for w in s["words"]) / n_words) if n_words else 0
    print(
        "  %d segments, %d words, %.1fs audio, %.1fs processing (%.2fx realtime)"
        % (len(segs), n_words, audio_len, elapsed, audio_len / elapsed)
    )
    print("  mean word confidence: %.3f" % mean_prob)
    print("  -> %s.txt / .json\n" % base)

    report.append(
        {
            "song": song,
            "source": SOURCE,
            "segments": len(segs),
            "words": n_words,
            "audio_sec": round(audio_len, 1),
            "proc_sec": round(elapsed, 1),
            "mean_word_conf": round(mean_prob, 3),
        }
    )

with open(os.path.join(OUT_DIR, "report_%s.json" % SOURCE), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
print("done.")
