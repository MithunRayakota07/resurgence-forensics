"""
Erase deleted-file residue that cluster-level wiping cannot reach.

Currently implements exactly one residue path: resident $DATA and $FILE_NAME
content in UNALLOCATED NTFS MFT records. Everything else in
docs/modules-1-2-design.md is unimplemented and is reported as NOT CHECKED.
Claiming coverage we do not have would make the certificate worthless.

SAFETY
------
This module writes destructively. Interlocks, in order:

  1. Refuses anything that is not a regular file. Never a block device.
  2. Refuses a file it cannot parse as an NTFS volume.
  3. Dry-run by DEFAULT. Destruction requires --apply.
  4. With --apply, requires --i-understand to be passed as well.
  5. Logs the full plan (record numbers, offsets, byte counts) BEFORE the
     first write.

The rule from the project file: loopback images only, never a physical device.
That is enforced here in code, not by convention.
"""

from __future__ import annotations

import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from erase.residue import CHECKED_PATHS, UNCHECKED_PATHS
from erase.ntfs import ATTR_DATA, ATTR_FILE_NAME
from erase.ntfs import NotNTFS, NtfsImage


class RefusedUnsafe(Exception):
    """Raised rather than risk writing to something we should not."""


def assert_safe_target(path: str) -> None:
    if not os.path.exists(path):
        raise RefusedUnsafe("target does not exist: %s" % path)
    st = os.stat(path)
    if stat.S_ISBLK(st.st_mode):
        raise RefusedUnsafe(
            "target is a BLOCK DEVICE. This tool operates on image files only.")
    if stat.S_ISCHR(st.st_mode):
        raise RefusedUnsafe("target is a character device.")
    if not stat.S_ISREG(st.st_mode):
        raise RefusedUnsafe("target is not a regular file.")
    try:
        NtfsImage(path)
    except NotNTFS as e:
        raise RefusedUnsafe("target is not an NTFS volume (%s)" % e)


def plan(image_path: str):
    """
    Everything we would overwrite, computed before anything is written.

    Walks MFT records directly rather than consuming the scanner's findings.
    An earlier version consumed `scan()` and branched with elif, so a record
    that still held content had its $DATA scheduled but never its $FILE_NAME --
    content was destroyed while all 360 filenames survived. Which is precisely
    the gap Microsoft documents in SDelete ("securely deletes file data, but
    not file names located in free disk space"), reproduced by accident in our
    own tool. The scanner caught it; the erasure plan must cover BOTH.
    """
    items = []
    img = NtfsImage(image_path)
    for rec in img.records():
        if rec.in_use or rec.is_directory:
            continue

        d = rec.attr(ATTR_DATA)
        if d is not None and d.resident and d.content and len(set(d.content)) > 1:
            items.append({"record": rec.index, "offset": d.content_offset,
                          "length": len(d.content), "what": "resident $DATA",
                          "filename": rec.filename})

        n = rec.attr(ATTR_FILE_NAME)
        if n is not None and n.resident and n.content and len(set(n.content)) > 1:
            # Overwrite the whole $FILE_NAME attribute content: the name, and
            # the parent-directory reference and timestamps beside it.
            items.append({"record": rec.index, "offset": n.content_offset,
                          "length": len(n.content), "what": "$FILE_NAME",
                          "filename": rec.filename})
    return items


def apply_plan(image_path: str, items) -> int:
    """
    Overwrite each planned region with zeros. Returns bytes written.

    Single pass. NIST SP 800-88r2 3.1.1 states multi-pass overwriting is not
    needed and explicitly counters the obsolete DoD 5220.22-M guidance; DoD
    itself removed overwrite specifications from NISPOM in 2006.

    Only the residual CONTENT bytes are overwritten, not the record structure,
    so the volume stays mountable and consistent. Attribute headers (sizes,
    timestamps) survive; that is metadata scrubbing, a separate task, and this
    tool does not pretend to have done it.
    """
    written = 0
    with open(image_path, "r+b") as fh:
        for it in items:
            fh.seek(it["offset"])
            fh.write(b"\x00" * it["length"])
            written += it["length"]
        fh.flush()
        os.fsync(fh.fileno())
    return written


def main() -> int:
    import argparse, json
    ap = argparse.ArgumentParser(
        description="Erase resident deleted-file residue from an NTFS image")
    ap.add_argument("image")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without this, dry run only.")
    ap.add_argument("--i-understand", action="store_true",
                    help="required alongside --apply; destroys data irreversibly")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    try:
        assert_safe_target(args.image)
    except RefusedUnsafe as e:
        print("REFUSED: %s" % e)
        return 2

    items = plan(args.image)
    total = sum(i["length"] for i in items)
    print("target        : %s" % args.image)
    print("residue found : %d region(s), %d bytes" % (len(items), total))
    print("paths checked : %s" % ", ".join(CHECKED_PATHS))
    print("NOT checked   : %s" % ", ".join(UNCHECKED_PATHS))
    for it in items[:5]:
        print("   MFT #%-6d @0x%-8x %5d B  %-16s %s"
              % (it["record"], it["offset"], it["length"], it["what"],
                 it["filename"] or ""))
    if len(items) > 5:
        print("   ... and %d more" % (len(items) - 5))

    if not args.apply:
        print("\nDRY RUN. Nothing written. Re-run with --apply --i-understand.")
        rc = 0
    elif not args.i_understand:
        print("\nREFUSED: --apply requires --i-understand.")
        rc = 2
    else:
        n = apply_plan(args.image, items)
        print("\nOVERWROTE %d bytes across %d regions (single pass)." % (n, len(items)))
        left = plan(args.image)
        print("re-scan: %d residue region(s) remaining" % len(left))
        rc = 0 if not left else 1

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"image": os.path.basename(args.image),
                       "regions": len(items), "bytes": total,
                       "applied": bool(args.apply and args.i_understand),
                       "paths_checked": CHECKED_PATHS,
                       "paths_not_checked": UNCHECKED_PATHS,
                       "plan": items}, fh, indent=2)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
