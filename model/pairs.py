"""
Adjacency pair mining for the compressed-data AUC experiment.

The question this exists to answer
----------------------------------
Phase 0's discriminator is hand-written: photometric continuity across the
seam of a JPEG. On the NIST corpus it FAILED -- inter-fragment fill was
photometrically indistinguishable from real image data (jump.jpg: true rows
median 0.917, fill rows median 0.890). So the whole project now rests on a
question we have never actually measured:

    Can a LEARNED model tell "block B immediately follows block A" from
    "block B is from the same file but elsewhere"?

And crucially, can it do so on COMPRESSED data? Entropy coding exists to make
the next byte unpredictable. If the answer is no for compressed formats, the
architecture must route by entropy -- decoder-led for compressed, model-led
for low-entropy -- and the headline claim has to be restated.

Two rules that make or break this experiment
--------------------------------------------
1. NEGATIVES COME FROM THE SAME FILE. If negatives are drawn from different
   files the model can score them by file type alone and the AUC becomes a
   restatement of FFT-75 classification -- a number that looks great and
   means nothing for sequencing.

2. THE SPLIT IS BY FILE, NOT BY BLOCK. Blocks from one file leaking across
   train and test would let the model memorise a document rather than learn
   adjacency.

Data is govdocs1 -- one million real government documents, the standard
corpus in this field. Deliberately not our own generated files.
"""

from __future__ import annotations

import os
import random
import zipfile

BLOCK = 4096
SEAM = 512          # bytes either side of the join, for the seam-only variant

# Extensions grouped by what we expect the entropy coding to do to us.
COMPRESSED = {".jpg", ".jpeg", ".png", ".gif", ".zip", ".docx", ".xlsx", ".pptx", ".gz"}
LOW_ENTROPY = {".txt", ".html", ".htm", ".xml", ".csv", ".log", ".c", ".java", ".js"}
MIXED = {".pdf", ".doc", ".xls", ".ppt", ".ps", ".rtf"}
UNCOMPRESSED = {".bmp", ".wav", ".tif", ".tiff"}

ALL_EXTS = COMPRESSED | LOW_ENTROPY | MIXED | UNCOMPRESSED


def group_of(ext: str) -> str:
    ext = ext.lower()
    if ext in COMPRESSED:
        return "compressed"
    if ext in LOW_ENTROPY:
        return "low-entropy"
    if ext in UNCOMPRESSED:
        return "uncompressed"
    return "mixed"


# ------------------------------------------------------------- collection ---

def collect_from_zips(zip_paths, min_size=BLOCK * 6, max_per_ext=400,
                      max_bytes_per_file=2 << 20):
    """
    Pull file bytes straight out of the govdocs1 thread zips.

    Returns {ext: [(name, data), ...]}. Files shorter than a handful of blocks
    are useless for adjacency (too few pairs) and are skipped.
    """
    out = {}
    for zp in zip_paths:
        try:
            z = zipfile.ZipFile(zp)
        except Exception:
            continue
        for info in z.infolist():
            if info.is_dir() or info.file_size < min_size:
                continue
            ext = os.path.splitext(info.filename)[1].lower()
            if ext not in ALL_EXTS:
                continue
            bucket = out.setdefault(ext, [])
            if len(bucket) >= max_per_ext:
                continue
            try:
                with z.open(info) as fh:
                    data = fh.read(max_bytes_per_file)
            except Exception:
                continue
            if len(data) >= min_size:
                bucket.append((info.filename, data))
    return out


def split_by_file(files, test_frac=0.3, seed=0):
    """Split a list of (name, data) by FILE. Never by block."""
    rnd = random.Random(seed)
    idx = list(range(len(files)))
    rnd.shuffle(idx)
    cut = int(len(idx) * (1 - test_frac))
    return [files[i] for i in idx[:cut]], [files[i] for i in idx[cut:]]


# ------------------------------------------------------------ pair mining ---

def _blocks(data: bytes, lo: int = 0):
    n = (len(data) - lo) // BLOCK
    return [(lo + i * BLOCK) for i in range(n)]


def mine_pairs(files, per_file=12, min_gap=3, seed=0, region_start=None,
               max_gap=None):
    """
    Build (A, B, label) triples.

    label 1 : B is the block immediately after A
    label 0 : B is from the SAME FILE but at least `min_gap` blocks away

    `max_gap` bounds how far a negative may sit from the true successor. This
    is the difference between an easy experiment and an operational one. With
    negatives drawn from anywhere in the file, much of what any scorer detects
    is REGIONAL similarity -- same chapter, same sheet, same object stream --
    not adjacency. In real carving the competitors are the blocks physically
    around the true successor, so `max_gap=20` reproduces the actual decision
    the search has to make. Every number is expected to fall under it; that
    fall is the point.

    `region_start` optionally restricts sampling to bytes at or beyond a
    per-file offset -- used to isolate a JPEG's entropy stream from its
    header, because a header block is highly predictable and would flatter
    the compressed-format result.
    """
    rnd = random.Random(seed)
    pos, neg = [], []
    for name, data in files:
        lo = 0
        if region_start is not None:
            lo = region_start.get(name, 0)
            lo = (lo // BLOCK) * BLOCK
        offs = _blocks(data, lo)
        if len(offs) < min_gap + 3:
            continue
        for _ in range(per_file):
            i = rnd.randrange(0, len(offs) - 1)
            a = data[offs[i]: offs[i] + BLOCK]
            b = data[offs[i + 1]: offs[i + 1] + BLOCK]
            if len(a) < BLOCK or len(b) < BLOCK:
                continue
            pos.append((a, b))

            # hard negative: same file, far enough away to be a real fragment
            if max_gap is None:
                choices = [j for j in range(len(offs))
                           if abs(j - (i + 1)) >= min_gap]
            else:
                # hardest realistic competitors: blocks flanking the true
                # successor. Exclude only the anchor and the successor itself.
                nlo = max(0, (i + 1) - max_gap)      # not `lo`: that is the
                nhi = min(len(offs), (i + 1) + max_gap + 1)   # region offset
                choices = [j for j in range(nlo, nhi) if j != i and j != i + 1]
            if not choices:
                continue
            j = rnd.choice(choices)
            c = data[offs[j]: offs[j] + BLOCK]
            if len(c) == BLOCK:
                neg.append((a, c))
    return pos, neg


def jpeg_scan_starts(files):
    """Byte offset where each JPEG's entropy stream begins (after the header)."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from carve.validators import jpeg as J
    out = {}
    for name, data in files:
        try:
            out[name] = J.parse_header(data, 0).scan_start
        except Exception:
            out[name] = 0
    return out
