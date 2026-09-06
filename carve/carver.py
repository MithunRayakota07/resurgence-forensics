"""
Top-level carving entry point: find headers, reassemble each file.

Deliberately NOT importing the ground-truth manifest. The carver must run on
runtime signals only -- decoder validity, photometric continuity, allocation
prior, path margin. SHA-256 against the original exists in bench/score.py and
nowhere else. In a real case there is no original to compare against, so a
tool that leans on one is measuring nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carve.beam import BeamCarver
from carve.blocks import ClusterView
from carve.priors.base import get_prior


def confidence(r) -> float:
    """
    Provisional runtime confidence in [0, 1].

    Phase 0 places holder: a monotone squash of the signals we actually have
    at runtime. It is NOT calibrated -- "0.9" here does not yet mean "90% of
    such files are byte-exact". Phase 2 replaces this with a fitted binary
    head plus split conformal, and until then the UI must label it
    UNCALIBRATED rather than quote it as a probability.
    """
    if not r.ok:
        return 0.0
    c = 0.55                                    # decoded every MCU, saw EOI
    if r.margin == float("inf"):
        c += 0.25                               # no competing complete path
    else:
        c += 0.25 * min(1.0, r.margin / 50.0)
    c += 0.20 * min(1.0, 40.0 / max(1.0, abs(r.score) / max(1, len(r.clusters))))
    return round(min(0.99, c), 3)


def carve_image(image_path: str, prior_name: str = "locality", beam_width: int = 8,
                cluster_size: int = 4096, collect_trace: bool = True,
                out_dir: str = None, progress=None) -> dict:
    view = ClusterView(image_path, cluster_size)
    prior = get_prior(prior_name)
    headers = view.find_jpeg_headers()

    if progress:
        progress({"event": "scan", "clusters": len(view), "headers": headers})

    results = []
    for hi, hc in enumerate(headers):
        carver = BeamCarver(view, prior, beam_width=beam_width,
                            collect_trace=collect_trace)
        if progress:
            progress({"event": "carve_start", "index": hi, "header_cluster": hc})
        r = carver.carve(hc)

        rec = {
            "header_cluster": hc,
            "ok": r.ok,
            "reason": r.reason,
            "clusters": r.clusters,
            "n_fragments": r.n_fragments,
            "mcus": r.mcus,
            "total_mcus": r.total_mcus,
            "score": round(r.score, 2),
            "margin": (None if r.margin == float("inf") else round(r.margin, 2)),
            "confidence": confidence(r),
            "confidence_calibrated": False,
            "elapsed_s": round(r.elapsed_s, 3),
            "candidates_scored": r.candidates_scored,
            "decoder_calls": r.decoder_calls,
            "size_bytes": len(r.data),
            "sha256": hashlib.sha256(r.data).hexdigest() if r.data else None,
            "trace": r.trace if collect_trace else [],
        }
        if out_dir and r.data:
            os.makedirs(out_dir, exist_ok=True)
            name = "carved_%03d_c%d.jpg" % (hi, hc)
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(r.data)
            rec["file"] = name
        results.append(rec)
        if progress:
            progress({"event": "carve_done", "index": hi, "ok": r.ok,
                      "clusters": r.clusters, "confidence": rec["confidence"]})

    return {
        "image": os.path.basename(image_path),
        "cluster_size": cluster_size,
        "total_clusters": len(view),
        "prior": prior.name,
        "prior_is_measured": prior.is_measured,
        "beam_width": beam_width,
        "headers_found": headers,
        "files": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Carve fragmented JPEGs from a disk image")
    ap.add_argument("image")
    ap.add_argument("--prior", default="locality", choices=["locality", "uniform"])
    ap.add_argument("--beam", type=int, default=8)
    ap.add_argument("--out", default=None, help="write recovered files here")
    ap.add_argument("--json", default=None, help="write full result JSON here")
    ap.add_argument("--no-trace", action="store_true")
    args = ap.parse_args()

    res = carve_image(args.image, args.prior, args.beam,
                      collect_trace=not args.no_trace, out_dir=args.out)

    print("image      : %s (%d clusters)" % (res["image"], res["total_clusters"]))
    tag = ("" if res["prior_is_measured"]
           else " (ablation control)" if res["prior"] == "uniform"
           else " (hand-set, not fitted)")
    print("prior      : %s%s" % (res["prior"], tag))
    print("headers    : %s" % res["headers_found"])
    for f in res["files"]:
        spans, run = [], None
        for c in f["clusters"]:
            if run and c == run[1] + 1:
                run[1] = c
            else:
                if run:
                    spans.append(run)
                run = [c, c]
        if run:
            spans.append(run)
        span_s = ", ".join("c%d..%d" % (a, b) if a != b else "c%d" % a for a, b in spans)
        print("  header c%-5d %-4s  %d frag  %d/%d MCUs  conf=%.2f%s  %.2fs  "
              "%d cand  [%s]"
              % (f["header_cluster"], "OK" if f["ok"] else "FAIL",
                 f["n_fragments"], f["mcus"], f["total_mcus"],
                 f["confidence"], "" if f["confidence_calibrated"] else "*",
                 f["elapsed_s"], f["candidates_scored"], span_s))
        if not f["ok"]:
            print("      reason: %s" % f["reason"])
        elif f["sha256"]:
            print("      sha256: %s" % f["sha256"])
    print("  * confidence is UNCALIBRATED in Phase 0")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(res, fh, indent=2)
        print("json       : %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
