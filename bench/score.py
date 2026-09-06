"""
Scoring against ground truth.

This is the ONLY place SHA-256 comparison against the original file is
allowed. It is an evaluation instrument, never a runtime signal: in a real
case there is no original, so a carver that consults one is measuring
nothing. Keeping the separation mechanical rather than a matter of
discipline is the point of putting it in its own module.

Metrics, because byte-exactness alone is too blunt to steer development:

  exact          SHA-256 identical to the original. The headline number.
  cluster_iou    Jaccard overlap of recovered vs true cluster sets.
  order_acc      Fraction of recovered clusters in the correct position.
  prefix         Longest correct leading run of clusters.
  renders        Does a real decoder (Pillow) open it, and at full size.
  ssim           Perceptual similarity to the original, for partial credit.
"""

from __future__ import annotations

import hashlib
import io
import json
import os

import numpy as np
from PIL import Image


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _gray(img: Image.Image, size) -> np.ndarray:
    return np.asarray(img.convert("L").resize(size, Image.BILINEAR), dtype=np.float64)


def ssim(a: Image.Image, b: Image.Image) -> float:
    """Global SSIM on 8x8 windows. Partial credit for 'mostly right'."""
    size = (min(a.width, b.width), min(a.height, b.height))
    if size[0] < 8 or size[1] < 8:
        return 0.0
    x, y = _gray(a, size), _gray(b, size)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    k = 8
    h, w = (size[1] // k) * k, (size[0] // k) * k
    x, y = x[:h, :w], y[:h, :w]
    xb = x.reshape(h // k, k, w // k, k).transpose(0, 2, 1, 3).reshape(-1, k * k)
    yb = y.reshape(h // k, k, w // k, k).transpose(0, 2, 1, 3).reshape(-1, k * k)
    mx, my = xb.mean(1), yb.mean(1)
    vx, vy = xb.var(1), yb.var(1)
    cxy = ((xb - mx[:, None]) * (yb - my[:, None])).mean(1)
    s = ((2 * mx * my + C1) * (2 * cxy + C2)) / ((mx ** 2 + my ** 2 + C1) * (vx + vy + C2))
    return float(np.clip(s.mean(), -1, 1))


def renders(data: bytes):
    """(opened_ok, width, height, decoded_fully)."""
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
    except Exception:
        return False, 0, 0, False
    try:
        im.load()
        return True, w, h, True
    except Exception:
        return True, w, h, False


def score_one(recovered: bytes, truth_path: str, rec_clusters, true_clusters) -> dict:
    with open(truth_path, "rb") as f:
        truth = f.read()

    out = {
        "exact": sha256(recovered) == sha256(truth),
        "sha_recovered": sha256(recovered),
        "sha_truth": sha256(truth),
        "size_recovered": len(recovered),
        "size_truth": len(truth),
    }

    rset, tset = set(rec_clusters), set(true_clusters)
    out["cluster_iou"] = round(len(rset & tset) / max(1, len(rset | tset)), 4)
    hits = sum(1 for i, c in enumerate(rec_clusters)
               if i < len(true_clusters) and true_clusters[i] == c)
    out["order_acc"] = round(hits / max(1, len(true_clusters)), 4)
    pref = 0
    for i, c in enumerate(rec_clusters):
        if i < len(true_clusters) and true_clusters[i] == c:
            pref += 1
        else:
            break
    out["prefix_clusters"] = pref
    out["true_clusters"] = len(true_clusters)

    ok, w, h, full = renders(recovered)
    out["renders"] = ok
    out["renders_fully"] = full
    out["dims"] = [w, h]

    if ok:
        try:
            a = Image.open(io.BytesIO(recovered))
            a.load()
        except Exception:
            a = None
        if a is not None:
            b = Image.open(io.BytesIO(truth))
            b.load()
            out["ssim"] = round(ssim(a, b), 4)
        else:
            out["ssim"] = None
    else:
        out["ssim"] = None
    return out


def match_to_truth(rec_clusters, manifest) -> dict:
    """Attribute a recovered file to the ground-truth file it overlaps most."""
    best, best_iou = None, -1.0
    rset = set(rec_clusters)
    for f in manifest["files"]:
        tset = set(f["cluster_order"])
        iou = len(rset & tset) / max(1, len(rset | tset))
        if iou > best_iou:
            best, best_iou = f, iou
    return best


def score_run(carve_result: dict, manifest_path: str, images_dir: str) -> dict:
    with open(manifest_path) as f:
        manifest = json.load(f)

    rows = []
    for rec in carve_result["files"]:
        if not rec.get("ok"):
            rows.append({"header_cluster": rec["header_cluster"], "ok": False,
                         "reason": rec.get("reason", ""), "exact": False})
            continue
        truth = match_to_truth(rec["clusters"], manifest)
        path = os.path.join(images_dir, "truth_" + truth["name"])
        data_path = rec.get("_data_path")
        with open(data_path, "rb") as f:
            data = f.read()
        s = score_one(data, path, rec["clusters"], truth["cluster_order"])
        s.update({"header_cluster": rec["header_cluster"], "ok": True,
                  "matched": truth["name"], "confidence": rec.get("confidence"),
                  "n_fragments_true": truth["n_fragments"],
                  "n_fragments_found": rec.get("n_fragments")})
        rows.append(s)

    n = len(rows)
    ex = sum(1 for r in rows if r.get("exact"))
    return {
        "image": carve_result.get("image"),
        "prior": carve_result.get("prior"),
        "files_expected": len(manifest["files"]),
        "files_attempted": n,
        "files_exact": ex,
        "exact_rate": round(ex / max(1, len(manifest["files"])), 4),
        "rows": rows,
    }
