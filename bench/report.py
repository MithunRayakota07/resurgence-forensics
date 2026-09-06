"""
Build the comparison table: us vs PhotoRec / Foremost / Scalpel, same images.

Every tool is scored the same way against the same ground truth. Where a
baseline wins, the table says so -- an honest table is worth far more than a
flattering one, and a flattering one gets destroyed by the first evaluator
who runs the tool themselves.

Emits bench/out/report.json, which the API and the UI both consume, so the
screen can never show a number the harness did not produce.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from bench.score import renders, score_one, sha256, ssim

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out")


def load(p):
    with open(p) as f:
        return json.load(f)


def best_match(data: bytes, truths: list):
    """
    Attribute a recovered file to the ground-truth file it most resembles.

    Baseline carvers emit files with no indication of which original they were
    trying to recover, so attribution has to be perceptual: decode both and
    compare. Exact SHA wins immediately; otherwise highest SSIM.
    """
    h = sha256(data)
    for t in truths:
        if t["sha256"] == h:
            return t, 1.0, True
    ok, _w, _h, _full = renders(data)
    if not ok:
        return (truths[0] if truths else None), 0.0, False
    try:
        a = Image.open(io.BytesIO(data))
        a.load()
    except Exception:
        try:
            a = Image.open(io.BytesIO(data))
            a.draft(None, a.size)
        except Exception:
            return (truths[0] if truths else None), 0.0, False
    best, best_s = None, -1.0
    for t in truths:
        with open(t["_path"], "rb") as f:
            b = Image.open(io.BytesIO(f.read()))
            b.load()
        try:
            s = ssim(a, b)
        except Exception:
            s = 0.0
        if s > best_s:
            best, best_s = t, s
    return best, max(0.0, best_s), False


def score_baseline(run: dict, truths: list) -> dict:
    """Best recovered artefact per ground-truth file, for one baseline tool."""
    per_truth = {t["name"]: {"exact": False, "ssim": 0.0, "renders": False,
                             "size": 0, "file": None} for t in truths}
    for p in run["files"]:
        with open(p, "rb") as f:
            data = f.read()
        if len(data) < 512:
            continue
        t, s, exact = best_match(data, truths)
        if t is None:
            continue
        ok, _w, _h, full = renders(data)
        cur = per_truth[t["name"]]
        cand = {"exact": exact, "ssim": round(s, 4), "renders": ok,
                "renders_fully": full, "size": len(data),
                "file": os.path.relpath(p, ROOT).replace("\\", "/")}
        if (cand["exact"], cand["ssim"]) > (cur["exact"], cur["ssim"]):
            per_truth[t["name"]] = cand
    return {
        "tool": run["tool"],
        "elapsed_s": run["elapsed_s"],
        "n_output_files": len(run["files"]),
        "per_file": per_truth,
        "exact": sum(1 for v in per_truth.values() if v["exact"]),
    }


def score_ours(carve: dict, truths: list, carved_dir: str) -> dict:
    per_truth = {t["name"]: {"exact": False, "ssim": 0.0, "renders": False,
                             "size": 0, "file": None, "confidence": None,
                             "clusters": None} for t in truths}
    total_time = 0.0
    for rec in carve["files"]:
        total_time += rec.get("elapsed_s", 0.0)
        if not rec.get("ok") or not rec.get("file"):
            continue
        p = os.path.join(carved_dir, rec["file"])
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            data = f.read()
        t, s, exact = best_match(data, truths)
        if t is None:
            continue
        sc = score_one(data, t["_path"], rec["clusters"], t["cluster_order"])
        per_truth[t["name"]] = {
            "exact": sc["exact"], "ssim": sc.get("ssim") or 0.0,
            "renders": sc["renders"], "renders_fully": sc["renders_fully"],
            "size": len(data),
            "file": os.path.relpath(p, ROOT).replace("\\", "/"),
            "confidence": rec.get("confidence"),
            "confidence_calibrated": rec.get("confidence_calibrated", False),
            "clusters": rec["clusters"],
            "n_fragments": rec.get("n_fragments"),
            "cluster_iou": sc["cluster_iou"],
            "order_acc": sc["order_acc"],
        }
    return {"tool": "ours (beam + validator + prior)",
            "elapsed_s": round(total_time, 2),
            "n_output_files": sum(1 for r in carve["files"] if r.get("ok")),
            "per_file": per_truth,
            "exact": sum(1 for v in per_truth.values() if v["exact"])}


def build(kind: str) -> dict:
    images_dir = os.path.join(ROOT, "corpus", "images")
    man = load(os.path.join(images_dir, "%s.manifest.json" % kind))
    truths = []
    for t in man["files"]:
        t = dict(t)
        t["_path"] = os.path.join(images_dir, "truth_" + t["name"])
        truths.append(t)

    tools = []
    ours_path = os.path.join(OUT, "ours_%s.json" % kind)
    if os.path.exists(ours_path):
        tools.append(score_ours(load(ours_path), truths,
                                os.path.join(OUT, "ours_%s" % kind)))

    bl_path = os.path.join(OUT, "baselines_%s.json" % kind)
    if os.path.exists(bl_path):
        for run in load(bl_path)["runs"]:
            tools.append(score_baseline(run, truths))

    return {
        "kind": kind,
        "image": man["image"],
        "cluster_size": man["cluster_size"],
        "total_clusters": man["total_clusters"],
        "truth": [{"name": t["name"], "size_bytes": t["size_bytes"],
                   "n_fragments": t["n_fragments"], "sha256": t["sha256"],
                   "fragments": t["fragments"], "cluster_order": t["cluster_order"],
                   "file": os.path.relpath(t["_path"], ROOT).replace("\\", "/")}
                  for t in truths],
        "tools": tools,
    }


def print_table(rep: dict) -> None:
    names = [t["name"] for t in rep["truth"]]
    frag = {t["name"]: t["n_fragments"] for t in rep["truth"]}
    print("\n=== %s (%s) ===" % (rep["kind"].upper(), rep["image"]))
    print("ground truth: " + ", ".join("%s (%d fragments)" % (n, frag[n]) for n in names))
    head = "%-30s %7s %8s  " % ("tool", "exact", "time")
    head += "  ".join("%-22s" % n for n in names)
    print(head)
    print("-" * len(head))
    for t in rep["tools"]:
        row = "%-30s %3d/%-3d %7.1fs  " % (t["tool"], t["exact"], len(names),
                                           t["elapsed_s"])
        cells = []
        for n in names:
            v = t["per_file"][n]
            if v["exact"]:
                cells.append("%-22s" % "EXACT (sha match)")
            elif v["renders"]:
                cells.append("%-22s" % ("corrupt ssim=%.2f%s" %
                                        (v["ssim"], "" if v.get("renders_fully") else " trunc")))
            elif v["size"]:
                cells.append("%-22s" % "unreadable")
            else:
                cells.append("%-22s" % "nothing recovered")
        print(row + "  ".join(cells))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinds", default="easy,hard")
    args = ap.parse_args()
    full = {"images": []}
    for k in args.kinds.split(","):
        rep = build(k.strip())
        full["images"].append(rep)
        print_table(rep)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(full, f, indent=2)
    print("\nwrote %s" % os.path.join(OUT, "report.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
