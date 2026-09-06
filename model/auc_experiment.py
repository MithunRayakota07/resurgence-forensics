"""
The compressed-data AUC experiment.

Measures, per format, whether a learned model can answer:

    "Does block B immediately follow block A?"

against the only negative that matters -- another block FROM THE SAME FILE.

Why the answer decides the architecture
---------------------------------------
Entropy coding is designed to make the next byte unpredictable, so there is a
real possibility that learned adjacency simply does not work on compressed
data. Phase 0 already showed the hand-written photometric discriminator fails
on the NIST corpus. If the learned one fails too on compressed formats, then
the honest architecture routes by measured entropy -- decoder-led for
compressed, model-led for low-entropy -- and the headline claim changes.

Decision rule, fixed BEFORE running so it cannot be rationalised afterwards:

    AUC >= 0.75   learned adjacency works here; two-stage retrieve+rerank
                  as planned.
    0.60 - 0.75   useful only as a tiebreaker among decoder-valid candidates.
    AUC <  0.60   does not work; route by entropy and move the learned-model
                  demo to the low-entropy formats where the evidence lives.

Controls included so the headline number cannot flatter us:
  * a byte-histogram cosine baseline (no learning at all)
  * JPEG measured twice -- whole file, and entropy-stream only. Header blocks
    are highly predictable and would inflate the compressed-format result.
  * train/test split BY FILE
  * negatives always same-file
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model.pairs import (BLOCK, SEAM, collect_from_zips, group_of,
                         jpeg_scan_starts, mine_pairs, split_by_file)

torch.manual_seed(0)
np.random.seed(0)


# ------------------------------------------------------------------ model ---

class Encoder(nn.Module):
    """
    Small byte-level 1D CNN. One tower; A and B get separate instances.

    The first version ended ReLU -> AdaptiveMaxPool -> normalize and collapsed
    on every format: the final ReLU died, max-pool returned a zero vector,
    normalize kept it zero, so every logit was exactly 0, BCE sat at ln(2) =
    0.6931 from epoch 2, and AUC came out at exactly 0.500 everywhere. Dead
    units cannot recover, so the run was unrecoverable from the start.

    Now: GELU instead of ReLU (no dead region), BatchNorm for scale stability,
    mean+max pooling so a single dead channel cannot zero the representation,
    and a linear head after pooling rather than normalizing a raw activation.
    """

    def __init__(self, dim=128, emb=16, width=128):
        super().__init__()
        self.emb = nn.Embedding(256, emb)
        self.net = nn.Sequential(
            nn.Conv1d(emb, 64, 9, stride=4, padding=4), nn.BatchNorm1d(64), nn.GELU(),
            nn.Conv1d(64, width, 9, stride=4, padding=4), nn.BatchNorm1d(width), nn.GELU(),
            nn.Conv1d(width, width, 9, stride=4, padding=4), nn.BatchNorm1d(width), nn.GELU(),
        )
        self.head = nn.Linear(width * 2, dim)

    def forward(self, x):                      # x: (B, L) uint8 as long
        h = self.net(self.emb(x).transpose(1, 2))
        h = torch.cat([h.mean(-1), h.max(-1).values], dim=1)
        return torch.nn.functional.normalize(self.head(h), dim=1)


class Adjacency(nn.Module):
    """
    Two towers: one reads the END of A, one reads the START of B.

    Separate towers because the relation is directional -- "what does this
    block lead into" is a different question from "what does this block follow
    from" -- and a shared tower would be forced to answer both with one
    representation.
    """

    def __init__(self, dim=128):
        super().__init__()
        self.a = Encoder(dim)
        self.b = Encoder(dim)
        self.scale = nn.Parameter(torch.tensor(5.0))

    def forward(self, xa, xb):
        return (self.a(xa) * self.b(xb)).sum(1) * self.scale


class CrossEncoder(nn.Module):
    """
    Joint model over the SEAM: [last SEAM bytes of A || first SEAM bytes of B].

    The bi-encoder scores a 128-d cosine between two INDEPENDENTLY encoded
    blocks. That is a retrieval architecture -- it has to compress everything
    that might matter about A into a vector before it ever sees B, so it
    cannot represent "these particular bytes continue those particular
    bytes". Measured, it lost to a 256-bin byte histogram on 9 of 10 formats,
    including plain text.

    A cross-encoder reads both sides together and can attend across the join,
    which is where the evidence lives. It cannot be indexed, so in the real
    system it is a RERANKER over bi-encoder candidates -- never the retriever.
    A channel flag marks which side each byte came from.
    """

    def __init__(self, emb=24, width=128):
        super().__init__()
        self.emb = nn.Embedding(256, emb)
        self.side = nn.Embedding(2, emb)
        self.net = nn.Sequential(
            nn.Conv1d(emb, 64, 9, stride=2, padding=4), nn.BatchNorm1d(64), nn.GELU(),
            nn.Conv1d(64, width, 9, stride=2, padding=4), nn.BatchNorm1d(width), nn.GELU(),
            nn.Conv1d(width, width, 9, stride=2, padding=4), nn.BatchNorm1d(width), nn.GELU(),
            nn.Conv1d(width, width, 9, stride=2, padding=4), nn.BatchNorm1d(width), nn.GELU(),
        )
        self.head = nn.Sequential(nn.Linear(width * 2, 128), nn.GELU(), nn.Linear(128, 1))

    def forward(self, xa, xb):
        x = torch.cat([xa, xb], dim=1)                       # (B, 2*SEAM)
        sides = torch.cat([torch.zeros_like(xa), torch.ones_like(xb)], dim=1)
        h = (self.emb(x) + self.side(sides)).transpose(1, 2)
        h = self.net(h)
        h = torch.cat([h.mean(-1), h.max(-1).values], dim=1)
        return self.head(h).squeeze(-1)


# ------------------------------------------------------------------- data ---

def to_tensor(pairs, seam_only: bool):
    """(list[(bytes,bytes)]) -> (A, B) long tensors."""
    if not pairs:
        return None, None
    if seam_only:
        A = np.frombuffer(b"".join(a[-SEAM:] for a, _ in pairs), dtype=np.uint8)
        B = np.frombuffer(b"".join(b[:SEAM] for _, b in pairs), dtype=np.uint8)
        L = SEAM
    else:
        A = np.frombuffer(b"".join(a for a, _ in pairs), dtype=np.uint8)
        B = np.frombuffer(b"".join(b for _, b in pairs), dtype=np.uint8)
        L = BLOCK
    # Keep bytes as uint8 and widen per batch. Storing .long() up front is an
    # 8x blow-up: at 18k pairs of 4096-byte blocks that is ~1.2 GB per tensor
    # before training even starts.
    return (torch.from_numpy(A.copy()).view(-1, L),
            torch.from_numpy(B.copy()).view(-1, L))


def auc(scores, labels) -> float:
    """Rank-based AUC; no sklearn dependency, ties handled."""
    s = np.asarray(scores, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int32)
    n_pos, n_neg = int((y == 1).sum()), int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=np.float64)
    ranks[order] = np.arange(1, len(s) + 1)
    # average ranks for ties
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt)); np.add.at(sums, inv, ranks)
    ranks = (sums / cnt)[inv]
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def histogram_baseline(pairs_pos, pairs_neg):
    """
    Control: cosine similarity of 256-bin byte histograms. No learning.

    If the trained model cannot beat this, it has learned nothing that simple
    byte statistics do not already give us.
    """
    def hist(b):
        h = np.bincount(np.frombuffer(b, dtype=np.uint8), minlength=256).astype(np.float64)
        n = np.linalg.norm(h)
        return h / n if n else h

    sc, lb = [], []
    for grp, lab in ((pairs_pos, 1), (pairs_neg, 0)):
        for a, b in grp:
            sc.append(float(hist(a) @ hist(b)))
            lb.append(lab)
    return auc(sc, lb)


# -------------------------------------------------------------- train/eval ---

def run_one(name, train_pos, train_neg, test_pos, test_neg, seam_only,
            epochs=6, bs=128, lr=3e-4, log=print, arch="bi"):
    if len(train_pos) < 64 or len(test_pos) < 32:
        return None

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = (CrossEncoder() if arch == "cross" else Adjacency()).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    lossf = nn.BCEWithLogitsLoss()

    trA_p, trB_p = to_tensor(train_pos, seam_only)
    trA_n, trB_n = to_tensor(train_neg, seam_only)
    A = torch.cat([trA_p, trA_n]); B = torch.cat([trB_p, trB_n])
    Y = torch.cat([torch.ones(len(trA_p)), torch.zeros(len(trA_n))])

    t0 = time.time()
    for ep in range(epochs):
        perm = torch.randperm(len(A))
        tot = 0.0
        model.train()
        for i in range(0, len(A), bs):
            idx = perm[i:i + bs]
            xa = A[idx].to(dev).long(); xb = B[idx].to(dev).long(); y = Y[idx].to(dev)
            opt.zero_grad()
            loss = lossf(model(xa, xb), y)
            loss.backward(); opt.step()
            tot += float(loss.detach()) * len(idx)
        last_loss = tot / len(A)
        log("      epoch %d/%d loss=%.4f (%.0fs)" % (ep + 1, epochs, last_loss, time.time() - t0))

    # Refuse to report a number from a collapsed model. BCE parked at ln(2)
    # means constant output, which yields a meaningless AUC of exactly 0.5.
    collapsed = abs(last_loss - 0.6931) < 5e-3

    model.eval()
    teA_p, teB_p = to_tensor(test_pos, seam_only)
    teA_n, teB_n = to_tensor(test_neg, seam_only)
    tA = torch.cat([teA_p, teA_n]); tB = torch.cat([teB_p, teB_n])
    tY = np.concatenate([np.ones(len(teA_p)), np.zeros(len(teA_n))])
    scores = []
    with torch.no_grad():
        for i in range(0, len(tA), 512):
            scores.append(model(tA[i:i + 512].to(dev).long(),
                                tB[i:i + 512].to(dev).long()).cpu().numpy())
    a = auc(np.concatenate(scores), tY)
    return {"auc": a, "collapsed": collapsed, "final_loss": round(last_loss, 4)}


def verdict(a: float) -> str:
    if a != a:
        return "n/a"
    if a >= 0.75:
        return "WORKS"
    if a >= 0.60:
        return "tiebreaker only"
    return "DOES NOT WORK"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips", default="corpus/govdocs_raw/*.zip")
    ap.add_argument("--per-file", type=int, default=10)
    ap.add_argument("--max-per-ext", type=int, default=300)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--out", default="bench/out/auc_results.json")
    ap.add_argument("--max-gap", type=int, default=None,
                    help="bound negatives to within N blocks of the true "
                         "successor (operational difficulty)")
    args = ap.parse_args()

    zips = sorted(glob.glob(args.zips))
    print("zips: %s" % [os.path.basename(z) for z in zips])
    print("negatives: %s" % ("ANYWHERE in file (regional)" if args.max_gap is None
                             else "within +/-%d blocks of the true successor "
                                  "(OPERATIONAL)" % args.max_gap))
    print("device: %s" % ("cuda" if torch.cuda.is_available() else "cpu"))
    t0 = time.time()
    byext = collect_from_zips(zips, max_per_ext=args.max_per_ext)
    print("collected: %s (%.0fs)"
          % ({k: len(v) for k, v in sorted(byext.items())}, time.time() - t0))

    results = []
    targets = [(e, f) for e, f in sorted(byext.items()) if len(f) >= 20]

    for ext, files in targets:
        tr, te = split_by_file(files)
        variants = [(ext, None)]
        if ext in (".jpg", ".jpeg"):
            variants.append((ext + " [entropy-stream only]", "scan"))

        for label, mode in variants:
            rs_tr = rs_te = None
            if mode == "scan":
                rs_tr, rs_te = jpeg_scan_starts(tr), jpeg_scan_starts(te)
            trp, trn = mine_pairs(tr, args.per_file, seed=1, region_start=rs_tr,
                                  max_gap=args.max_gap)
            tep, ten = mine_pairs(te, args.per_file, seed=2, region_start=rs_te,
                                  max_gap=args.max_gap)
            if len(trp) < 64 or len(tep) < 32:
                print("  %-32s skipped (too few pairs)" % label)
                continue

            print("  %-32s train=%d/%d test=%d/%d" % (label, len(trp), len(trn), len(tep), len(ten)))
            base = histogram_baseline(tep, ten)
            full = run_one(label, trp, trn, tep, ten, seam_only=False,
                           epochs=args.epochs, arch="bi")
            seam = run_one(label, trp, trn, tep, ten, seam_only=True,
                           epochs=args.epochs, arch="bi")
            cross = run_one(label, trp, trn, tep, ten, seam_only=True,
                            epochs=args.epochs, arch="cross")

            def val(r):
                return None if r is None or r["collapsed"] else r["auc"]

            fv, sv, cv = val(full), val(seam), val(cross)
            usable = [x for x in (fv, sv, cv) if x is not None]
            row = {"format": label, "group": group_of(ext), "n_files": len(files),
                   "n_test_pairs": len(tep) + len(ten),
                   "auc_hist_baseline": base,
                   "auc_full_block": fv, "auc_seam_only": sv, "auc_cross_seam": cv,
                   "beats_baseline": bool(usable and max(usable) > base),
                   "full_collapsed": bool(full and full["collapsed"]),
                   "seam_collapsed": bool(seam and seam["collapsed"]),
                   "full_loss": full["final_loss"] if full else None,
                   "seam_loss": seam["final_loss"] if seam else None,
                   "verdict": verdict(max(usable)) if usable else "COLLAPSED - no result"}
            results.append(row)
            fmt = lambda x: "COLLAPS" if x is None else "%.3f" % x
            print("      hist=%.3f  bi-full=%s  bi-seam=%s  CROSS=%s   -> %s%s"
                  % (base, fmt(fv), fmt(sv), fmt(cv), row["verdict"],
                     "" if row["beats_baseline"] else "  (loses to histogram)"))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"results": results}, f, indent=2)

    print("\n%-34s %-13s %7s %7s %7s  %s"
          % ("format", "group", "hist", "full", "seam", "verdict"))
    print("-" * 92)
    for r in sorted(results, key=lambda r: (r["group"], r["format"])):
        g = lambda k: "COLLAPS" if r[k] is None else "%7.3f" % r[k]
        print("%-34s %-13s %7.3f %7s %7s %7s  %s%s"
              % (r["format"], r["group"], r["auc_hist_baseline"],
                 g("auc_full_block"), g("auc_seam_only"), g("auc_cross_seam"),
                 r["verdict"], "" if r["beats_baseline"] else "  (< histogram)"))
    print("\nwrote %s  (total %.0fs)" % (args.out, time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
