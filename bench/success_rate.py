"""
Measure the carver's success RATE over randomised layouts.

Why this exists
---------------
Until now the evidence was "1/1 on hard.img, 2/2 on easy.img" -- two layouts
we hand-placed once. That is not a rate, and it cannot answer the obvious
question: how often does this actually work?

It matters because success turns out to be INSTANCE-DEPENDENT. Holding file
size, decoy density and layout shape fixed and changing only which photo is
the evidence and how the filler is seeded flips the outcome. One arrangement
recovers byte-exact; another reaches 2503 of 2508 MCUs through a wrong
11-fragment assembly. Reporting two hand-picked wins while that is true would
be misleading.

What it does
------------
Builds N independent 3-fragment OUT-OF-ORDER layouts, each randomising:

  * which photograph is the evidence
  * where the two fragmentation points fall
  * how far the backward jump reaches, and the forward gaps
  * the filler seed, so the surrounding decoy photos differ

then carves each and scores it byte-exact against ground truth. Results are
written incrementally, so a long run is still useful if interrupted.

Usage
-----
    resurgence-success-rate --src "C:/Users/me/Pictures" --n 30
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carve.carver import carve_image
from corpus.generate.real_photos import (RealPhotoFiller, find_photos, usable,
                                         write_manifest)
from corpus.generate.synthetic import CLUSTER, DiskBuilder, split_at_clusters


def build_instance(out_dir, name, data, decoys, total_clusters, rnd):
    """One randomised out-of-order layout: fragment 2 lands before fragment 1."""
    cut_a = rnd.uniform(0.25, 0.42)
    cut_b = rnd.uniform(0.58, 0.75)
    pieces = split_at_clusters(data, [cut_a, cut_b])
    if len(pieces) != 3:
        return None
    f1, f2, f3 = pieces

    start = rnd.randint(60, 140)
    gap_back = rnd.randint(8, 24)      # forward gap after the middle fragment
    gap_fwd = rnd.randint(5, 20)

    b = DiskBuilder(total_clusters, RealPhotoFiller(decoys, seed=rnd.randrange(1 << 30)))
    b.place(name, 1, f2, at=start)
    b.place(name, 0, f1, at=b.cursor + gap_back)
    b.place(name, 2, f3, at=b.cursor + gap_fwd)

    raw = b.finish()
    path = os.path.join(out_dir, "inst.img")
    with open(path, "wb") as fh:
        fh.write(raw)
    man = write_manifest(out_dir, path, "inst", total_clusters, raw, b.placed,
                         {name: data})
    return path, man


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Measure byte-exact success rate over randomised layouts")
    ap.add_argument("--src", required=True, help="directory of real JPEGs")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--size-mib", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "out", "success_rate.json"))
    args = ap.parse_args()

    loaded = []
    for p in find_photos(args.src):
        got = usable(p)
        if got is not None:
            loaded.append((os.path.basename(p), got[0]))
    if len(loaded) < 4:
        print("need at least 4 usable photos, found %d" % len(loaded))
        return 1

    print("%d usable photo(s); running %d randomised out-of-order layouts\n"
          % (len(loaded), args.n))
    print("%-4s %-34s %-6s %-9s %-6s %-8s %s"
          % ("#", "evidence", "cl", "MCUs", "frags", "cand", "result"))
    print("-" * 92)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    work = os.path.join(os.path.dirname(args.out), "_success_work")
    os.makedirs(work, exist_ok=True)
    total_clusters = args.size_mib * 1024 * 1024 // CLUSTER

    rnd = random.Random(args.seed)
    results = []
    passes = 0
    t0 = time.time()

    for i in range(args.n):
        name, data = loaded[rnd.randrange(len(loaded))]
        decoys = [d for n, d in loaded if n != name]
        built = build_instance(work, name, data, decoys, total_clusters, rnd)
        if built is None:
            continue
        path, man = built
        truth = man["files"][0]

        res = carve_image(path, prior_name="locality", collect_trace=False)
        f = res["files"][0] if res["files"] else None
        ok = bool(f and f["ok"] and f["sha256"] == truth["sha256"])
        order_ok = bool(f and f["clusters"] == truth["cluster_order"])
        passes += ok

        print("%-4d %-34s %-6d %-9s %-6s %-8s %s"
              % (i + 1, name[:34], len(truth["cluster_order"]),
                 "%s/%s" % (f["mcus"], f["total_mcus"]) if f else "-",
                 f["n_fragments"] if f else "-",
                 f["candidates_scored"] if f else "-",
                 "PASS" if ok else "fail (%s)" % (f["reason"] if f else "no header")))
        sys.stdout.flush()

        results.append({
            "instance": i + 1,
            "evidence": name,
            "clusters": len(truth["cluster_order"]),
            "byte_exact": ok,
            "cluster_order_exact": order_ok,
            "mcus": f["mcus"] if f else None,
            "total_mcus": f["total_mcus"] if f else None,
            "n_fragments_found": f["n_fragments"] if f else None,
            "n_fragments_true": truth["n_fragments"],
            "candidates_scored": f["candidates_scored"] if f else None,
            "elapsed_s": f["elapsed_s"] if f else None,
            "reason": None if ok else (f["reason"] if f else "no header found"),
        })

        with open(args.out, "w") as fh:
            json.dump({"n_completed": len(results), "byte_exact": passes,
                       "size_mib": args.size_mib, "seed": args.seed,
                       "instances": results}, fh, indent=2)

    n = len(results)
    elapsed = time.time() - t0
    print("\n%d/%d byte-exact (%.0f%%) over %d randomised out-of-order layouts"
          % (passes, n, 100.0 * passes / n if n else 0, n))
    times = [r["elapsed_s"] for r in results if r["elapsed_s"]]
    if times:
        times.sort()
        print("per-instance carve time: median %.0fs  min %.0fs  max %.0fs"
              % (times[len(times) // 2], times[0], times[-1]))
    print("total wall clock: %.0f min" % (elapsed / 60))
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
