"""Client for the deployed Modal app — measures what T-0.3 asks for.

Functions are looked up by name rather than imported, which is how the real
backend will call them in T-3.3, so the numbers here reflect the eventual path.

The spec's target is a total under 90 seconds. "Total" is measured the way a
user would feel it: from the moment the client sends the file to the moment the
stems are back, including cold start and both transfers.
"""
import io
import json
import os
import statistics
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import modal

APP = "karuki-separation"
SONG = "דרוש נא (1)"
SRC = os.path.join("input", "דרוש נא (1).mp3")
OUT = os.path.join("output", "remote")


def f(name):
    return modal.Function.from_name(APP, name)


def cmd_ping():
    t = time.time()
    r = f("ping").remote()
    print("ping -> %s   (%.2fs round trip)" % (r, time.time() - t))


def cmd_gpu():
    t = time.time()
    r = f("gpu_info").remote()
    print("gpu_info -> %s   (%.2fs round trip)" % (r, time.time() - t))


def one_run(data, label):
    t0 = time.time()
    r = f("separate").remote(os.path.basename(SRC), data)
    total = time.time() - t0
    tm = r["timings"]
    # everything not spent inside the container is wake-up plus transfer
    overhead = total - tm["in_container_s"]
    print("  %-8s cold=%-5s total=%6.1fs  in-container=%5.1fs  "
          "(model %4.1f / separate %5.1f / encode %4.1f)  overhead=%5.1fs"
          % (label, r["cold_start"], total, tm["in_container_s"],
             tm["model_load_s"], tm["separation_s"], tm["encode_s"], overhead))
    return {"label": label, "cold_start": r["cold_start"], "total_s": round(total, 2),
            "overhead_s": round(overhead, 2), **tm}, r["stems"]


def cmd_measure(n=5):
    data = open(SRC, "rb").read()
    print("קובץ: %s (%.1f MB)\n" % (SRC, len(data) / 1e6))
    rows, stems = [], None
    for i in range(n):
        row, s = one_run(data, "run %d" % (i + 1))
        rows.append(row)
        if stems is None:
            stems = s
        # a pause between runs; back-to-back calls would all reuse one warm
        # container and hide the cold-start behaviour this task is measuring
        if i == 0:
            time.sleep(5)

    os.makedirs(OUT, exist_ok=True)
    for name, blob in stems.items():
        open(os.path.join(OUT, "%s.mp3" % name), "wb").write(blob)

    warm = [r for r in rows if not r["cold_start"]]
    cold = [r for r in rows if r["cold_start"]]
    print()
    print("=" * 70)
    if cold:
        print("  התעוררות קרה: %.1fs סה\"כ" % cold[0]["total_s"])
    if warm:
        print("  ריצות חמות: חציון %.1fs · טווח %.1f–%.1f"
              % (statistics.median(r["total_s"] for r in warm),
                 min(r["total_s"] for r in warm),
                 max(r["total_s"] for r in warm)))
    best = min(r["total_s"] for r in rows)
    worst = max(r["total_s"] for r in rows)
    print("  יעד האפיון: מתחת ל-90 שניות · נמדד %.1f–%.1f" % (best, worst))
    print("  %s" % ("✓ עומד ביעד" if worst < 90 else "✗ חורג מהיעד"))
    print("  קו בסיס מקומי (CPU): 144s לשיר הזה")
    if warm:
        print("  שיפור מול CPU: פי %.1f"
              % (144 / statistics.median(r["total_s"] for r in warm)))

    json.dump(rows, open(os.path.join(OUT, "report_gpu.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n  -> %s" % os.path.join(OUT, "report_gpu.json"))


def cmd_one():
    """A single call — used after an idle gap to catch a genuine second cold start.

    T-0.3.3 asks that the second wake-up and onwards stay under 15s, which is
    only meaningful once the container has actually scaled down. Back-to-back
    calls reuse a warm container and would answer a different question.
    """
    data = open(SRC, "rb").read()
    row, _ = one_run(data, "cold?")
    print()
    if row["cold_start"]:
        print("  התעוררות אמיתית · טעינת מודל %.1fs" % row["model_load_s"])
        print("  %s" % ("✓ המשקלים נטענו מהמטמון" if row["model_load_s"] < 3
                        else "✗ נראה שהמשקלים הורדו מחדש"))
    else:
        print("  המכולה עדיין חמה — צריך להמתין עוד לפני מדידת התעוררות")
    json.dump(row, open(os.path.join(OUT, "cold_start.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "ping"
    {"ping": cmd_ping, "gpu": cmd_gpu, "one": cmd_one,
     "measure": lambda: cmd_measure(int(sys.argv[2]) if len(sys.argv) > 2 else 5)}[which]()
