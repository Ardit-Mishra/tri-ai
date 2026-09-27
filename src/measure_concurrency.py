"""
Measure what actually happens when N local workers hit one GPU at once.

WHY THIS EXISTS
---------------
Two independent research passes landed on the same open question: nobody knows this box's real
concurrency ceiling. Ollama is documented to degrade under concurrent load (no continuous
batching), and VRAM cost scales with parallel_slots x context_length -- which is expensive on a
12GB card. Every downstream decision (how many workers, whether "5 repos in parallel" is faster
than serial) rests on a number nobody has measured.

So measure it. This is the project's own rule applied to its own design:

    Never ask a model to derive a fact from raw output. Have the command emit the fact.

WHAT IT REPORTS
---------------
For each concurrency level N: wall-clock for the batch, aggregate tokens/sec, per-request latency,
peak VRAM, and -- the number that actually decides the design -- SPEEDUP vs running the same N
requests serially. A speedup below 1.0 means parallelism is making things WORSE, which is a real
possible outcome and the one worth knowing before building a swarm on top of it.

Writes concurrency_results.json. Reports failures as failures; never substitutes a plausible value.

    python measure_concurrency.py --model qwen3.5:4b --levels 1,2,3,4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

PROMPT = (
    "List exactly five common causes of a failing unit test. "
    "One short line each, no preamble, no numbering."
)


def gpu_mib() -> int | None:
    """Used VRAM in MiB, or None if nvidia-smi is unavailable. None is not zero."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return None
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def one_request(host: str, model: str, num_predict: int, out: list, idx: int) -> None:
    body = json.dumps({
        "model": model,
        "prompt": PROMPT,
        "stream": False,
        "options": {"num_predict": num_predict, "temperature": 0},
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read())
        out[idx] = {
            "ok": True,
            "seconds": round(time.time() - t0, 2),
            # Ollama's own counters, not a guess derived from the text.
            "eval_count": d.get("eval_count"),
            "eval_duration_s": round((d.get("eval_duration") or 0) / 1e9, 2),
            "load_duration_s": round((d.get("load_duration") or 0) / 1e9, 2),
        }
    except Exception as exc:
        out[idx] = {"ok": False, "seconds": round(time.time() - t0, 2),
                    "error": f"{type(exc).__name__}: {exc}"}


def run_level(host: str, model: str, n: int, num_predict: int) -> dict:
    results: list = [None] * n
    peak = {"mib": gpu_mib() or 0}
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            m = gpu_mib()
            if m and m > peak["mib"]:
                peak["mib"] = m
            time.sleep(0.25)

    watch = threading.Thread(target=sampler, daemon=True)
    watch.start()

    t0 = time.time()
    threads = [threading.Thread(target=one_request, args=(host, model, num_predict, results, i))
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = round(time.time() - t0, 2)

    stop.set()
    watch.join(timeout=2)

    ok = [r for r in results if r and r.get("ok")]
    failed = [r for r in results if r and not r.get("ok")]
    tokens = sum(r.get("eval_count") or 0 for r in ok)
    return {
        "concurrency": n,
        "wall_seconds": wall,
        "completed": len(ok),
        "failed": len(failed),
        "failures": [r.get("error") for r in failed],
        "total_tokens": tokens,
        "aggregate_tokens_per_sec": round(tokens / wall, 1) if wall else None,
        "per_request_seconds": [r.get("seconds") for r in results if r],
        "mean_request_seconds": round(sum(r["seconds"] for r in ok) / len(ok), 2) if ok else None,
        "peak_vram_mib": peak["mib"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--levels", default="1,2,3,4")
    ap.add_argument("--num-predict", type=int, default=160)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--out", default="concurrency_results.json")
    args = ap.parse_args()

    levels = [int(x) for x in args.levels.split(",")]

    # Warm the model once so the first level is not paying the load cost for everyone.
    print(f"warming {args.model} ...", flush=True)
    warm: list = [None]
    one_request(args.host, args.model, 16, warm, 0)
    if not warm[0]["ok"]:
        raise SystemExit(f"model will not respond, refusing to report timings: {warm[0]['error']}")

    runs = []
    for n in levels:
        best = None
        for rep in range(args.repeats):
            r = run_level(args.host, args.model, n, args.num_predict)
            # Keep the best wall-clock: we want the ceiling this box CAN reach, and a background
            # process stealing the GPU mid-run would otherwise be reported as a concurrency limit.
            if best is None or r["wall_seconds"] < best["wall_seconds"]:
                best = r
            print(f"  n={n} rep={rep+1} wall={r['wall_seconds']}s "
                  f"tok/s={r['aggregate_tokens_per_sec']} vram={r['peak_vram_mib']}MiB "
                  f"failed={r['failed']}", flush=True)
        runs.append(best)

    # Speedup vs doing the same N requests one after another, using the measured n=1 latency.
    base = next((r for r in runs if r["concurrency"] == 1), None)
    for r in runs:
        if base and base["mean_request_seconds"] and r["wall_seconds"]:
            serial = base["mean_request_seconds"] * r["concurrency"]
            r["serial_estimate_seconds"] = round(serial, 2)
            r["speedup_vs_serial"] = round(serial / r["wall_seconds"], 2)
        else:
            r["speedup_vs_serial"] = None

    report = {
        "model": args.model,
        "host": args.host,
        "num_predict": args.num_predict,
        "repeats": args.repeats,
        "note": "speedup_vs_serial < 1.0 means concurrency is HURTING throughput",
        "runs": runs,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print("\n  n   wall     tok/s   speedup   peak VRAM   failed")
    for r in runs:
        print(f"  {r['concurrency']}  {r['wall_seconds']:>6}s  {str(r['aggregate_tokens_per_sec']):>6}"
              f"   {str(r['speedup_vs_serial']):>6}x   {r['peak_vram_mib']:>6} MiB   {r['failed']}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
