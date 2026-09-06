"""
Derive ground truth for the NIST CFReDS "File Carving Graphic Files" (2023)
corpus, and emit it in the same manifest format `corpus/generate/synthetic.py`
produces so the existing scoring harness works unchanged.

Why this file exists
--------------------
Our own disk images are the softest part of our evidence: we wrote the
generator, chose the fragmentation, chose the filler, and our tool wins. That
is a demonstration, not a benchmark.

This corpus is different. It is the US government's own test data -- the exact
set cited by the CFTT test reports for Magnet AXIOM 7.5 (Jul 2024), FTK 8.0
(Jan 2024), Autopsy 4.21 (Feb 2024), R-Studio T80+ 9.3 (Jul 2024) and Forensic
Explorer 5.6.8 (Mar 2025). Running on it lets us put our numbers beside theirs
instead of beside numbers we produced ourselves.

How ground truth is derived
---------------------------
NIST does not ship a machine-readable layout. It ships the dd images and the
ORIGINAL source files, and its own analysis scripts work by block hashing. We
do the same, independently:

  1. Cut every source file into SECTOR_SIZE blocks and hash each one.
  2. Sweep the dd image sector by sector, hashing as we go.
  3. A hash hit says "sector S of the image holds block B of file F".
  4. Sort each file's hits by block index -> the true cluster order, including
     any out-of-order or backward runs.

This gives us the answer sheet without trusting anyone's documentation, and it
is the same technique NIST uses, so disagreements are detectable.

Caveat, handled explicitly below: blocks that repeat (long runs of 0x00, or
padding shared between files) are ambiguous and would corrupt the mapping.
They are detected and excluded rather than guessed at.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict

SECTOR_SIZE = 512          # CFReDS layouts are sector-aligned, not 4 KiB
AMBIGUITY_LIMIT = 1        # a block occurring in >1 source position is unusable


def _h(b: bytes) -> bytes:
    """Short, fast content hash. Truncated blake2b is ample for block identity."""
    return hashlib.blake2b(b, digest_size=16).digest()


# --------------------------------------------------------------- indexing ---

def index_sources(src_dir: str, sector: int = SECTOR_SIZE, exts=None):
    """
    Hash every sector-sized block of every source file.

    Returns (index, files) where
        index : block_hash -> (filename, block_index)   [unique blocks only]
        files : filename   -> {"size", "sha256", "n_blocks", "path"}

    Blocks that appear more than once anywhere in the corpus are dropped from
    the index. A run of zero bytes is identical in every file that contains
    one, so keeping it would let a single ambiguous sector claim membership in
    several files at once and silently poison the ground truth.
    """
    occurrences = defaultdict(list)
    files = {}

    for name in sorted(os.listdir(src_dir)):
        path = os.path.join(src_dir, name)
        if not os.path.isfile(path):
            continue
        if exts and os.path.splitext(name)[1].lower() not in exts:
            continue
        with open(path, "rb") as f:
            data = f.read()
        n = 0
        for i in range(0, len(data), sector):
            blk = data[i : i + sector]
            if len(blk) < sector:
                # A trailing partial block does not occupy a full sector on
                # disk and cannot be matched by a whole-sector sweep. It is
                # excluded from the index, so the recovered cluster list may
                # legitimately omit the file's final partial sector.
                break
            occurrences[_h(blk)].append((name, i // sector))
            n += 1
        files[name] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "n_blocks": n,
            "path": path,
        }

    index = {h: v[0] for h, v in occurrences.items() if len(v) <= AMBIGUITY_LIMIT}
    dropped = len(occurrences) - len(index)
    return index, files, dropped


def locate_in_image(image_path: str, index, sector: int = SECTOR_SIZE):
    """
    Sweep the dd image and record where each indexed block lives.

    Returns hits: filename -> {block_index: sector_index}
    """
    hits = defaultdict(dict)
    size = os.path.getsize(image_path)
    with open(image_path, "rb") as f:
        for s in range(size // sector):
            blk = f.read(sector)
            if len(blk) < sector:
                break
            found = index.get(_h(blk))
            if found is not None:
                name, bi = found
                # first occurrence wins; a later duplicate would be a copy
                hits[name].setdefault(bi, s)
    return hits


# --------------------------------------------------------------- manifest ---

def bridge_gaps(got: dict) -> int:
    """
    Fill single-block holes left by ambiguity exclusion.

    Some blocks are byte-identical across several source files -- a shared
    EXIF/JFIF header block, or identical padding -- so they are excluded from
    the index to stop one sector claiming membership in several files. That is
    the right call for correctness, but it punches one-sector holes that split
    a contiguous run in two and inflate the reported fragment count. NIST's own
    README says each fragmented file has "two or four fragments"; before this
    fix we reported five for leaf.jpg, which would have misdescribed the
    benchmark in our favour (more fragments = harder-looking).

    A hole is filled only when it is provably determined: block b-1 sits at
    sector s-1 and block b+1 sits at sector s+1, so block b can only be at
    sector s. No guessing.
    """
    filled = 0
    changed = True
    while changed:
        changed = False
        for b in sorted(got):
            nxt = b + 2
            if (b + 1) not in got and nxt in got and got[nxt] - got[b] == 2:
                got[b + 1] = got[b] + 1
                filled += 1
                changed = True
    return filled


def _runs(sectors):
    """Collapse a sector list into contiguous runs -> fragment count."""
    if not sectors:
        return []
    runs = [[sectors[0], sectors[0]]]
    for s in sectors[1:]:
        if s == runs[-1][1] + 1:
            runs[-1][1] = s
        else:
            runs.append([s, s])
    return runs


def build_manifest(image_path: str, src_dir: str, sector: int = SECTOR_SIZE,
                   exts=None, min_coverage: float = 0.90) -> dict:
    """
    Produce a manifest in the same schema as corpus/generate/synthetic.py.

    Only files whose blocks are found with at least `min_coverage` of their
    blocks located are included. A partially located file means either the
    file is not in this image, or the layout is not sector-aligned -- in both
    cases guessing would produce a wrong answer sheet, so we omit it and say
    so in the summary.
    """
    index, files, dropped = index_sources(src_dir, sector, exts)
    hits = locate_in_image(image_path, index, sector)

    entries, skipped = [], []
    for name, meta in files.items():
        got = hits.get(name, {})
        if meta["n_blocks"] == 0:
            continue
        bridged = bridge_gaps(got)
        coverage = len(got) / meta["n_blocks"]

        # Claim the file's trailing PARTIAL sector.
        #
        # A file whose size is not a whole number of sectors ends with a short
        # block that shares its sector with whatever fill follows, so it can
        # never be found by whole-sector hashing and is absent from the index.
        # Omitting it makes the answer sheet one sector short of the truth --
        # which marks a CORRECT reconstruction as a failure. Measured on
        # stonehenge.jpg: the full true run decodes 19,596 of 19,602 MCUs and
        # the very next sector supplies the remaining 6 and lands on EOI.
        #
        # Safe because a fragment is contiguous by definition: the tail block
        # must sit in the sector immediately after the last full block.
        tail_sector = None
        if got and meta["size"] % sector:
            last_block = max(got)
            if last_block == meta["n_blocks"] - 1:      # final full block located
                cand = got[last_block] + 1
                if cand < os.path.getsize(image_path) // sector:
                    got[meta["n_blocks"]] = cand
                    tail_sector = cand
        if coverage < min_coverage:
            skipped.append({"name": name, "coverage": round(coverage, 3),
                            "blocks_found": len(got), "blocks_total": meta["n_blocks"]})
            continue
        order = [got[b] for b in sorted(got)]
        runs = _runs(order)
        entries.append({
            "name": name,
            "size_bytes": meta["size"],
            "sha256": meta["sha256"],
            "n_fragments": len(runs),
            "coverage": round(coverage, 4),
            "bridged_blocks": bridged,
            "tail_sector": tail_sector,
            "fragments": [{"fragment": i, "start_cluster": a, "n_clusters": b - a + 1}
                          for i, (a, b) in enumerate(runs)],
            "cluster_order": order,
            "out_of_order": any(runs[i + 1][0] < runs[i][0] for i in range(len(runs) - 1)),
        })

    return {
        "image": os.path.basename(image_path),
        "kind": os.path.splitext(os.path.basename(image_path))[0],
        "source": "NIST CFReDS 'File Carving Graphic Files' (2023)",
        "cluster_size": sector,
        "total_clusters": os.path.getsize(image_path) // sector,
        "size_bytes": os.path.getsize(image_path),
        "ambiguous_blocks_dropped": dropped,
        "files": sorted(entries, key=lambda e: e["fragments"][0]["start_cluster"]),
        "files_not_located": skipped,
    }


def summarise(manifest: dict) -> str:
    lines = ["%s  (%d sectors x %d B)" % (manifest["image"], manifest["total_clusters"],
                                          manifest["cluster_size"])]
    if not manifest["files"]:
        lines.append("  no source files located in this image")
    for e in manifest["files"]:
        spans = ", ".join("s%d..%d" % (f["start_cluster"],
                                       f["start_cluster"] + f["n_clusters"] - 1)
                          for f in e["fragments"])
        lines.append("  %-28s %8d B  %d frag%s  cov=%.1f%%  [%s]"
                     % (e["name"], e["size_bytes"], e["n_fragments"],
                        " OUT-OF-ORDER" if e["out_of_order"] else "",
                        e["coverage"] * 100, spans))
    if manifest["files_not_located"]:
        lines.append("  not located (%d):" % len(manifest["files_not_located"]))
        for s in manifest["files_not_located"][:12]:
            lines.append("    %-28s %d/%d blocks (%.0f%%)"
                         % (s["name"], s["blocks_found"], s["blocks_total"],
                            s["coverage"] * 100))
    if manifest["ambiguous_blocks_dropped"]:
        lines.append("  %d ambiguous blocks excluded from the index"
                     % manifest["ambiguous_blocks_dropped"])
    return "\n".join(lines)


def write_manifest(manifest: dict, out_dir: str, src_dir: str) -> str:
    """Write the manifest and copy the located originals as truth_ files."""
    os.makedirs(out_dir, exist_ok=True)
    for e in manifest["files"]:
        src = os.path.join(src_dir, e["name"])
        dst = os.path.join(out_dir, "truth_" + e["name"])
        if os.path.exists(src) and not os.path.exists(dst):
            with open(src, "rb") as a, open(dst, "wb") as b:
                b.write(a.read())
    path = os.path.join(out_dir, "%s.manifest.json" % manifest["kind"])
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    return path


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Derive ground truth for a CFReDS dd image by block hashing")
    ap.add_argument("image", help="path to the dd image")
    ap.add_argument("--src", required=True, help="directory of original source files")
    ap.add_argument("--out", default=None, help="where to write the manifest")
    ap.add_argument("--sector", type=int, default=SECTOR_SIZE)
    ap.add_argument("--ext", default=None,
                    help="comma-separated extensions to index, e.g. .jpg,.jpeg")
    ap.add_argument("--min-coverage", type=float, default=0.90)
    args = ap.parse_args()

    exts = None
    if args.ext:
        exts = {e if e.startswith(".") else "." + e
                for e in (x.strip().lower() for x in args.ext.split(","))}

    m = build_manifest(args.image, args.src, args.sector, exts, args.min_coverage)
    print(summarise(m))
    if args.out:
        p = write_manifest(m, args.out, args.src)
        print("manifest: %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
