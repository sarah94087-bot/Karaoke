"""T-0.3 — Demucs separation on a serverless GPU.

Covers T-0.3.1 (remote function responds) through T-0.3.4 (five measured runs).

Cost discipline: the free workspace has $1/month, not the $30 that comparison
sites advertise -- that tier needs a payment method. At ~$0.002 per song a T4
gives roughly 500 runs, so the budget is ample, but the GPU is deliberately a T4
rather than an A100: the spec's target is a 90-second total, not peak speed.

Weight caching is the whole point of T-0.3.3. Demucs downloads ~80MB of weights
to the torch hub cache on first use; without a persistent volume every cold
start would re-download them and the wake time could never meet the 15s target.
TORCH_HOME points at the mounted volume so the download happens exactly once.
"""
import time

import modal

# Bumped by hand when the deploy needs forcing: `modal deploy` compares a hash of
# the mount and will report "no changes detected" even after the file is edited.
BUILD = 5

app = modal.App("karuki-separation")

# Set when the container's Python process starts. The gap between this and the
# moment a call begins work is the real wake-up cost that T-0.3.3 asks about;
# model-load time alone understates it.
_BOOT = time.time()

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("demucs==4.1.0", "numpy", "soundfile", "torch", "torchaudio")
    # Demucs 4.1 fetches its weights from the Hugging Face Hub, not torch.hub, so
    # TORCH_HOME alone left the 84MB checkpoint in the container's ephemeral
    # filesystem and every cold start paid to download it again. HF_HOME is the
    # variable that actually redirects it onto the volume.
    .env({"TORCH_HOME": "/cache/torch", "HF_HOME": "/cache/hf"})
)

cache = modal.Volume.from_name("karuki-demucs-cache", create_if_missing=True)

# Set once per container. A call that sees it True is running on a cold start,
# which is how the wake time in T-0.3.4 is separated from the processing time.
_FRESH_CONTAINER = True


@app.function(image=image)
def ping():
    """T-0.3.1 — the smallest thing that proves the remote side answers."""
    import platform
    import sys
    return {
        "ok": True,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


@app.function(image=image, volumes={"/cache": cache}, timeout=900)
def where_are_weights():
    """Finds where Demucs actually writes its checkpoints.

    Setting TORCH_HOME was not enough: the volume ended up with an empty `torch`
    directory, so the download is landing somewhere else. Rather than guess at
    the right environment variable, this reports the real paths.
    """
    import os
    import torch
    from demucs.pretrained import get_model

    before = torch.hub.get_dir()
    get_model("htdemucs")           # forces the download

    # A filtered os.walk found nothing, which usually means the guess about
    # extensions or locations is wrong. Search the whole filesystem by size.
    import subprocess
    big = subprocess.run(
        ["find", "/", "-xdev", "-type", "f", "-size", "+5M",
         "-not", "-path", "*/site-packages/nvidia/*",
         "-not", "-path", "*/site-packages/torch/lib/*",
         "-printf", "%s\t%p\n"],
        capture_output=True, text=True).stdout.strip().split("\n")
    rows = []
    for line in big:
        if not line.strip():
            continue
        size, path = line.split("\t", 1)
        rows.append((round(int(size) / 1e6, 1), path))
    rows.sort(reverse=True)

    cache.commit()
    return {"TORCH_HOME": os.environ.get("TORCH_HOME"),
            "torch_hub_dir": before,
            "hub_exists": os.path.isdir(before),
            "hub_contents": os.listdir(before) if os.path.isdir(before) else None,
            "largest_files": rows[:15]}


@app.function(image=image, gpu="T4", volumes={"/cache": cache}, timeout=900)
def verify_cache():
    """Binary test: can the model load with the Hugging Face Hub set to offline?

    Timing alone cannot settle whether the weights come from the volume — a
    5-second load could be a download or a disk read. With HF_HUB_OFFLINE=1 a
    download is impossible, so a successful load proves the cache is being used
    and a failure proves it is not.
    """
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    boot_to_call = time.time() - _BOOT
    t = time.time()
    try:
        from demucs.pretrained import get_model
        get_model("htdemucs")
        ok, err = True, None
    except Exception as e:
        ok, err = False, "%s: %s" % (type(e).__name__, str(e)[:200])
    return {"loaded_offline": ok, "error": err,
            "load_s": round(time.time() - t, 2),
            "boot_to_call_s": round(boot_to_call, 2)}


@app.function(image=image, gpu="T4", volumes={"/cache": cache}, timeout=900)
def gpu_info():
    """Confirms a GPU is actually attached before any real work is sent."""
    import torch
    return {
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "torch": torch.__version__,
    }


@app.function(image=image, gpu="T4", volumes={"/cache": cache}, timeout=900)
def separate(filename: str, data: bytes) -> dict:
    """T-0.3.2 — audio in, four stems out, on the remote GPU."""
    global _FRESH_CONTAINER
    cold = _FRESH_CONTAINER
    _FRESH_CONTAINER = False

    import os
    import pathlib
    import subprocess

    t_entry = time.time()
    work = pathlib.Path("/tmp/work")
    work.mkdir(parents=True, exist_ok=True)
    src = work / filename
    src.write_bytes(data)

    t_model = time.time()
    import torch  # noqa: F401  (import cost belongs to model-load time)
    from demucs.api import Separator

    separator = Separator(model="htdemucs", device="cuda")
    model_ready = time.time() - t_model

    t_sep = time.time()
    origin, stems = separator.separate_audio_file(src)
    sep_time = time.time() - t_sep

    # Encoding was measured at 15.5s against 6.2s for the separation itself, so
    # the four stems are encoded concurrently instead of one after another.
    t_enc = time.time()
    import soundfile as sf
    procs = {}
    for name, tensor in stems.items():
        p = work / f"{name}.wav"
        sf.write(str(p), tensor.cpu().numpy().T, separator.samplerate)
        mp3 = work / f"{name}.mp3"
        procs[name] = (mp3, subprocess.Popen(
            ["ffmpeg", "-y", "-i", str(p), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    out = {}
    for name, (mp3, proc) in procs.items():
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed for {name}")
        out[name] = mp3.read_bytes()
    encode_time = time.time() - t_enc

    cache.commit()  # persist the downloaded weights for the next cold start

    return {
        "cold_start": cold,
        "stems": out,
        "timings": {
            "boot_to_call_s": round(t_entry - _BOOT, 2),
            "model_load_s": round(model_ready, 2),
            "separation_s": round(sep_time, 2),
            "encode_s": round(encode_time, 2),
            "in_container_s": round(time.time() - t_entry, 2),
        },
    }
