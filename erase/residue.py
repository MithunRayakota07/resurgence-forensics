"""
Residue scanner: find deleted-file content that cluster-level wiping cannot reach.

The measured claim
------------------
On a 256 MiB NTFS volume with 400 small case files, 360 deleted normally and
then a full free-space wipe applied:

    after deletion         360/360 files' content still readable  (100%)
    after free-space wipe  359/360 still readable                 (99.7%)

The one loss was reclaimed by the wiper's own temporary file. Cluster-level
wiping is not a partial defence here; it is very nearly no defence at all.

Why
---
NTFS stores a small file's data INSIDE its MFT record. No cluster is ever
allocated. Deleting the file clears one bit in the record header. "Wipe free
space" tools fill unallocated CLUSTERS -- and this content was never in a
cluster, so they walk straight past it.

A single-file test is misleading in the other direction: on a near-empty volume
the freed record is the obvious first-fit candidate and the next file created
takes it. That was measured too, and it is why this module reports a rate over
many records rather than a yes/no on one.

Scope: NTFS resident $DATA in unallocated MFT records. Other residue paths
(shadow copies, $LogFile, $UsnJrnl, thumbnail caches, SQLite WAL) are listed in
docs/modules-1-2-design.md and are NOT implemented here. This module reports
only what it actually checked.
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from erase.ntfs import ATTR_DATA, ATTR_FILE_NAME, NotNTFS, NtfsImage

# Paths this scanner knows how to check. Anything not listed is reported as
# NOT CHECKED rather than silently omitted -- a certificate that implies more
# coverage than was performed is worse than no certificate.
CHECKED_PATHS = ["ntfs:mft-resident-unallocated"]
UNCHECKED_PATHS = [
    "ntfs:$LogFile", "ntfs:$UsnJrnl", "ntfs:$I30-index-slack",
    "volume-shadow-copies", "file-slack", "thumbnail-cache",
    "sqlite-wal-journal", "pagefile/hiberfil", "ssd-overprovisioning", "hpa-dco",
]

PRINTABLE = set(range(32, 127)) | {9, 10, 13}


def _texty(b: bytes) -> float:
    if not b:
        return 0.0
    return sum(1 for c in b if c in PRINTABLE) / len(b)


class Finding:
    __slots__ = ("record", "offset", "length", "filename", "data", "kind")

    def __init__(self, record, offset, length, filename, data, kind):
        self.record = record
        self.offset = offset
        self.length = length
        self.filename = filename
        self.data = data
        self.kind = kind

    def as_dict(self, preview=64):
        return {
            "kind": self.kind,
            "mft_record": self.record,
            "image_offset": self.offset,
            "length": self.length,
            "filename": self.filename,
            "sha256": hashlib.sha256(self.data).hexdigest() if self.data else None,
            "preview": self.data[:preview].decode("utf-8", "replace") if self.data else "",
        }


def scan(image_path: str, min_len: int = 16, limit: int = None):
    """
    Yield Findings for resident content in UNALLOCATED MFT records.

    A record whose in-use bit is clear but whose $DATA attribute still carries
    content is, precisely, a deleted file whose data was never in a cluster.
    """
    img = NtfsImage(image_path)
    for rec in img.records(limit=limit):
        if rec.in_use or rec.is_directory:
            continue
        data = rec.resident_data
        name = rec.filename
        # Content that has already been overwritten is not residue. Without
        # this the scanner counts a zeroed $DATA attribute as a live finding
        # and reports residue remaining immediately after we erased it --
        # which would put a false statement on the certificate.
        if data and len(set(data)) <= 1:
            data = None
        if data and len(data) >= min_len:
            attr = rec.attr(ATTR_DATA)
            yield Finding(rec.index, attr.content_offset, len(data), name, data,
                          "resident-data")
        elif name and name.strip(chr(0)):
            # No content, but the NAME survives. Microsoft's own SDelete
            # documentation concedes it "securely deletes file data, but not
            # file names located in free disk space".
            attr = rec.attr(ATTR_FILE_NAME)
            yield Finding(rec.index, attr.content_offset if attr else rec.offset,
                          0, name, b"", "orphan-filename")


def summarise(image_path: str, limit: int = None) -> dict:
    try:
        findings = list(scan(image_path, limit=limit))
    except NotNTFS as e:
        return {"image": os.path.basename(image_path), "error": str(e)}
    content = [f for f in findings if f.kind == "resident-data"]
    names = [f for f in findings if f.kind == "orphan-filename"]
    return {
        "image": os.path.basename(image_path),
        "paths_checked": CHECKED_PATHS,
        "paths_not_checked": UNCHECKED_PATHS,
        "recoverable_resident_files": len(content),
        "orphan_filenames": len(names),
        "recoverable_bytes": sum(f.length for f in content),
        "findings": [f.as_dict() for f in content],
    }


def main() -> int:
    import argparse, json
    ap = argparse.ArgumentParser(
        description="Find deleted-file content surviving in unallocated MFT records")
    ap.add_argument("image")
    ap.add_argument("--json", default=None)
    ap.add_argument("--show", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N MFT records")
    args = ap.parse_args()

    s = summarise(args.image, limit=args.limit)
    if "error" in s:
        print("%s: %s" % (s["image"], s["error"]))
        return 1
    print("image                : %s" % s["image"])
    print("recoverable files    : %d  (%d bytes of deleted content)"
          % (s["recoverable_resident_files"], s["recoverable_bytes"]))
    print("orphan filenames     : %d" % s["orphan_filenames"])
    print("paths checked        : %s" % ", ".join(s["paths_checked"]))
    print("paths NOT checked    : %s" % ", ".join(s["paths_not_checked"]))
    for f in s["findings"][: args.show]:
        print("  MFT #%-6d @0x%-8x %5d B  %-22s %s"
              % (f["mft_record"], f["image_offset"], f["length"],
                 f["filename"] or "(name gone)",
                 f["preview"].replace("\n", " ")[:46]))
    if len(s["findings"]) > args.show:
        print("  ... and %d more" % (len(s["findings"]) - args.show))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(s, fh, indent=2)
        print("json: %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
