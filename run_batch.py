import csv
import os
import subprocess
import sys
import time
import wave
import contextlib

INPUT_DIR = "input"
OUTPUT_DIR = "output"
MODEL = "htdemucs"

def get_duration(path):
    try:
        with contextlib.closing(wave.open(path, 'r')) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            return frames / float(rate)
    except Exception:
        return None

rows = []
files = sorted(f for f in os.listdir(INPUT_DIR) if f.lower().endswith((".mp3", ".wav", ".m4a")))

for fname in files:
    in_path = os.path.join(INPUT_DIR, fname)
    print(f"=== Processing {fname} ===")
    start = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "demucs", "-n", MODEL, "-o", OUTPUT_DIR, in_path],
        capture_output=True, text=True
    )
    elapsed = time.time() - start
    stem_name = os.path.splitext(fname)[0]
    out_dir = os.path.join(OUTPUT_DIR, MODEL, stem_name)
    ok = os.path.isdir(out_dir) and os.path.isfile(os.path.join(out_dir, "vocals.wav"))

    # build no_vocals.wav
    if ok:
        ff = r"C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.exe"
        bass = os.path.join(out_dir, "bass.wav")
        drums = os.path.join(out_dir, "drums.wav")
        other = os.path.join(out_dir, "other.wav")
        no_vocals = os.path.join(out_dir, "no_vocals.wav")
        subprocess.run([ff, "-y", "-i", bass, "-i", drums, "-i", other,
                         "-filter_complex", "amix=inputs=3:duration=longest:dropout_transition=0",
                         no_vocals], capture_output=True, text=True)

    vocals_path = os.path.join(out_dir, "vocals.wav")
    duration = get_duration(vocals_path) if ok else None

    rows.append({
        "file": fname,
        "status": "ok" if ok else "FAILED",
        "duration_sec": round(duration, 1) if duration else "",
        "processing_sec": round(elapsed, 1),
        "realtime_factor": round(elapsed / duration, 2) if duration else "",
    })
    print(f"  status={rows[-1]['status']} duration={rows[-1]['duration_sec']}s processing={rows[-1]['processing_sec']}s")
    if not ok:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])

with open("output/report_timing.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=["file", "status", "duration_sec", "processing_sec", "realtime_factor"])
    writer.writeheader()
    writer.writerows(rows)

print("\nDone. Wrote output/report_timing.csv")
