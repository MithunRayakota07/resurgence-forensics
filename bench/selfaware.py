"""
Can the carver tell a good reconstruction from a bad one, using only what it
knows at runtime?

Why this exists
---------------
The carver reports `ok=True` when it decodes every MCU and lands on EOI.
`oak-snow.jpg` on CFReDS does both and is still wrong (CLAUDE.md 5h). So the
success flag is not trustworthy, and the question is whether any signal we
ALREADY compute separates a correct reconstruction from a wrong one.

This is deliberately not a new algorithm. It carves a mix of cases whose
ground truth we hold, records the runtime feature vector for each, and labels
it correct or incorrect afterwards. If some feature separates the two classes,
a fix exists. If none does, the success flag cannot be repaired from current
signals and that is worth knowing before anyone tries.

Ground truth is used ONLY to label, never as an input feature. Every feature
below is available in a real case with no original to compare against.

Usage
-----
    python bench/selfaware.py --out bench/out/selfaware.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carve.beam import BeamCarver
from carve.blocks import ClusterView
from carve.carver import confidence
from carve.priors.base import get_prior

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def features(r, view):
    """Everything knowable WITHOUT the original file."""
    n = max(1, len(r.clusters))
    return {
        "ok": bool(r.ok),
        "reason": r.reason,
        "n_clusters": len(r.clusters),
        "n_fragments": r.n_fragments,
        "mcus": r.mcus,
        "total_mcus": r.total_mcus,
        "mcu_completeness": (r.mcus / r.total_mcus) if r.total_mcus else 0.0,
        "score": round(r.score, 3),
        "score_per_cluster": round(r.score / n, 4),
        "margin": (None if r.margin == float("inf") else round(r.margin, 3)),
        "margin_is_inf": r.margin == float("inf"),
        "confidence": confidence(r),
        "candidates_scored": r.candidates_scored,
        "candidates_per_cluster": round(r.candidates_scored / n, 2),
        # the signal we most suspect: a file broken into many pieces
        "fragments_per_100_clusters": round(100.0 * r.n_fragments / n, 3),
        "mean_run_length": round(n / max(1, r.n_fragments), 2),
        "elapsed_s": round(r.elapsed_s, 1),
    }


def carve_one(view, prior, start):
    return BeamCarver(view, prior, beam_width=8, collect_trace=False).carve(start)


def run_manifest(image, manifest_path, prior, label_source, out, only=None):
    with open(manifest_path) as fh:
        man = json.load(fh)
    view = ClusterView(image, man["cluster_size"])

    for tf in man["files"]:
        if only and tf["name"] not in only:
            continue
        start = tf["cluster_order"][0]
        t0 = time.time()
        r = carve_one(view, prior, start)
        f = features(r, view)
        # ground truth used ONLY for the label
        f["correct"] = bool(r.ok and r.data_sha == tf["sha256"]) if hasattr(r, "data_sha") else None
        if f["correct"] is None:
            import hashlib
            got = hashlib.sha256(r.data).hexdigest() if r.data else None
            f["correct"] = bool(got == tf["sha256"])
        f["true_fragments"] = tf["n_fragments"]
        f["true_clusters"] = len(tf["cluster_order"])
        f["file"] = tf["name"]
        f["source"] = label_source
        out.append(f)
        print("  %-16s %-9s ok=%-5s frags=%-3d/%-3d  mcus=%s/%s  conf=%.2f  %s  %.0fs"
              % (tf["name"][:16], label_source, f["ok"], f["n_fragments"],
                 f["true_fragments"], f["mcus"], f["total_mcus"], f["confidence"],
                 "CORRECT" if f["correct"] else "WRONG", time.time() - t0))
        sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(REPO, "bench", "out", "selfaware.json"))
    ap.add_argument("--real-src", default=None,
                    help="folder of real photos, to add known-good instances")
    ap.add_argument("--real-n", type=int, default=8)
    ap.add_argument("--skip-cfreds", action="store_true")
    ap.add_argument("--skip-small", action="store_true",
                    help="skip the small synthetic and real-photo cases")
    ap.add_argument("--contig", action="store_true",
                    help="also carve CFReDS image-contig-jpg.dd. These are the SAME six "
                         "photographs laid out contiguously, so they are large AND "
                         "correct -- the size-matched control for the false positive.")
    args = ap.parse_args()

    prior = get_prior("locality")
    out = []

    # ---- the synthetic benchmark images: known to carve correctly ----------
    for kind in ([] if args.skip_small else ("easy", "hard")):
        img = os.path.join(REPO, "corpus", "images", "%s.img" % kind)
        man = img.replace(".img", ".manifest.json")
        if os.path.exists(img) and os.path.exists(man):
            print("=== %s.img ===" % kind)
            run_manifest(img, man, prior, "synthetic", out)

    # ---- CFReDS contiguous: LARGE and correct, the size-matched control ----
    if args.contig:
        cg_img = os.path.join(REPO, "corpus", "cfreds", "images", "image-contig-jpg.dd")
        cg_man = os.path.join(REPO, "corpus", "cfreds", "image-contig-jpg.manifest.json")
        if os.path.exists(cg_img) and os.path.exists(cg_man):
            print("=== CFReDS image-contig-jpg.dd (large, contiguous) ===")
            run_manifest(cg_img, cg_man, prior, "cfreds-contig", out)

    # ---- real-photo instances: known to carve correctly --------------------
    if args.real_src:
        from corpus.generate.real_photos import find_photos, usable
        from bench.success_rate import build_instance
        from corpus.generate.synthetic import CLUSTER

        loaded = []
        for p in find_photos(args.real_src):
            got = usable(p)
            if got:
                loaded.append((os.path.basename(p), got[0]))
        if len(loaded) >= 4:
            work = os.path.join(os.path.dirname(args.out), "_selfaware_work")
            os.makedirs(work, exist_ok=True)
            rnd = random.Random(7)
            print("=== %d real-photo instances ===" % args.real_n)
            for i in range(args.real_n):
                name, data = loaded[rnd.randrange(len(loaded))]
                decoys = [d for n_, d in loaded if n_ != name]
                built = build_instance(work, name, data, decoys,
                                       16 * 1024 * 1024 // CLUSTER, rnd)
                if built:
                    path, man = built
                    run_manifest(path, os.path.join(work, "inst.manifest.json"),
                                 prior, "real-photo", out)

    # ---- CFReDS: where the false positive lives ---------------------------
    if not args.skip_cfreds:
        cf_img = os.path.join(REPO, "corpus", "cfreds", "images", "image-frag-jpg.dd")
        cf_man = os.path.join(REPO, "corpus", "cfreds", "image-frag-jpg.manifest.json")
        if os.path.exists(cf_img) and os.path.exists(cf_man):
            print("=== CFReDS image-frag-jpg.dd ===")
            run_manifest(cf_img, cf_man, prior, "cfreds", out)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)

    # ---- the actual question ---------------------------------------------
    good = [f for f in out if f["correct"]]
    bad = [f for f in out if not f["correct"]]
    fp = [f for f in bad if f["ok"]]

    print("\n%d carves: %d correct, %d wrong" % (len(out), len(good), len(bad)))
    print("FALSE POSITIVES (ok=True but wrong): %d" % len(fp))
    for f in fp:
        print("   %s (%s)" % (f["file"], f["source"]))

    if not fp:
        print("\nNo false positives in this sample -- nothing to separate.")
        return 0

    print("\nDoes any runtime feature separate correct carves from false positives?")
    keys = ["n_fragments", "fragments_per_100_clusters", "mean_run_length",
            "score_per_cluster", "confidence", "candidates_per_cluster",
            "mcu_completeness", "margin_is_inf"]
    print("%-30s %-24s %-24s %s" % ("feature", "correct (min..max)", "false positive", "separates?"))
    print("-" * 100)
    for k in keys:
        gv = [f[k] for f in good if f.get(k) is not None]
        bv = [f[k] for f in fp if f.get(k) is not None]
        if not gv or not bv:
            continue
        gv = [float(x) for x in gv]
        bv = [float(x) for x in bv]
        overlap = not (max(bv) < min(gv) or min(bv) > max(gv))
        print("%-30s %-24s %-24s %s"
              % (k, "%.3f..%.3f" % (min(gv), max(gv)),
                 "%.3f..%.3f" % (min(bv), max(bv)),
                 "no -- OVERLAPS" if overlap else "YES, CLEAN SPLIT"))
    print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
