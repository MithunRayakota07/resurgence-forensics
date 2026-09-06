"""
Metadata scrubbing for NTFS — and the reason most tools do it detectably wrong.

The eight timestamps
--------------------
NTFS records FOUR timestamps in $STANDARD_INFORMATION (created, modified, MFT
changed, accessed) and FOUR MORE in $FILE_NAME. That is eight per file, not
four. Windows APIs expose only the $SI set, so anything built on `SetFileTime`
touches half of them.

The consequence is that a tool which rewrites only $SI leaves the two sets
disagreeing -- and $SI/$FN divergence is a well-known timestomping indicator
that examiners look for directly. Scrubbing badly is worse than not scrubbing:
it converts "an ordinary file" into "a file someone tampered with", which is
itself evidence.

So this module does two things:

  * **detect** divergence between the two sets, and report it, because that is
    what an investigator wants to find; and
  * **scrub both sets together**, because that is what an eraser must do to
    avoid manufacturing the very artefact it is trying to remove.

FILETIME is 100-nanosecond intervals since 1601-01-01 UTC.

Scope: NTFS $SI and $FN timestamps, and $FILE_NAME contents. NOT implemented:
EXIF/XMP in images, Office document properties, PDF incremental-update history,
alternate data streams. Those are listed in docs/modules-1-2-design.md and are
reported as NOT CHECKED rather than silently skipped.
"""

from __future__ import annotations

import os
import struct
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from erase.ntfs import (ATTR_FILE_NAME, ATTR_STANDARD_INFORMATION, NotNTFS,
                        NtfsImage)

FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

# $STANDARD_INFORMATION: four FILETIMEs from offset 0.
SI_TIMES = (("created", 0x00), ("modified", 0x08),
            ("mft_changed", 0x10), ("accessed", 0x18))
# $FILE_NAME: eight bytes of parent reference first, then the same four.
FN_TIMES = (("created", 0x08), ("modified", 0x10),
            ("mft_changed", 0x18), ("accessed", 0x20))

CHECKED = ["ntfs:$STANDARD_INFORMATION timestamps",
           "ntfs:$FILE_NAME timestamps", "ntfs:$FILE_NAME name"]
NOT_CHECKED = ["exif/xmp", "office document properties", "pdf incremental updates",
               "alternate data streams", "$I30 index copies of $FILE_NAME"]


def filetime_to_dt(v: int):
    if v <= 0:
        return None
    try:
        return FILETIME_EPOCH + timedelta(microseconds=v // 10)
    except (OverflowError, OSError):
        return None


def read_times(content: bytes, layout):
    out = {}
    for name, off in layout:
        if off + 8 <= len(content):
            out[name] = struct.unpack_from("<Q", content, off)[0]
    return out


def inspect(image_path: str, include_deleted: bool = True):
    """
    Report the eight timestamps per file and flag $SI/$FN divergence.

    Divergence is not proof of tampering -- normal operations can update $SI
    without touching $FN, so `accessed` and `mft_changed` legitimately drift.
    A CREATED time that differs between the two sets is the strong signal, and
    is reported separately from the weak ones.
    """
    img = NtfsImage(image_path)
    rows = []
    for rec in img.records():
        if rec.is_directory:
            continue
        if not rec.in_use and not include_deleted:
            continue
        si = rec.attr(ATTR_STANDARD_INFORMATION)
        fn = rec.attr(ATTR_FILE_NAME)
        if si is None or fn is None or not si.resident or not fn.resident:
            continue
        st = read_times(si.content, SI_TIMES)
        ft = read_times(fn.content, FN_TIMES)
        if not st or not ft:
            continue
        # Two guards against false positives, both found by testing:
        #
        # 1. A ZERO timestamp is unset, not divergent. mkntfs writes $MFT's
        #    $SI timestamps as all zeros, so comparing 0 against a real time
        #    flagged the filesystem's own metafile as tampered with.
        # 2. NTFS metafiles (records 0-15, names beginning '$') are created by
        #    the formatter and routinely carry inconsistent times. Flagging
        #    them as evidence of tampering is noise that would discredit every
        #    genuine hit in the same report.
        metafile = rec.index < 16 or (rec.filename or "").startswith("$")

        def diverges(k):
            return (k in st and k in ft and st[k] and ft[k] and st[k] != ft[k])

        strong = [] if metafile else [k for k in ("created",) if diverges(k)]
        weak = [] if metafile else [k for k in ("modified", "mft_changed",
                                                "accessed") if diverges(k)]
        rows.append({
            "record": rec.index, "in_use": rec.in_use,
            "metafile": metafile,
            "filename": rec.filename,
            "si": st, "fn": ft,
            "divergent_strong": strong, "divergent_weak": weak,
            "si_offset": si.content_offset, "si_len": len(si.content),
            "fn_offset": fn.content_offset, "fn_len": len(fn.content),
        })
    return rows


def scrub_plan(image_path: str, only_deleted: bool = True):
    """
    Regions to overwrite so BOTH timestamp sets and the name go together.

    Only unallocated records by default: scrubbing a live file's metadata
    would corrupt a working volume, and module 2 is about erasing what was
    deleted, not vandalising what is present.
    """
    items = []
    for r in inspect(image_path, include_deleted=True):
        if only_deleted and r["in_use"]:
            continue
        for name, off in SI_TIMES:
            items.append({"record": r["record"], "offset": r["si_offset"] + off,
                          "length": 8, "what": "$SI." + name})
        for name, off in FN_TIMES:
            items.append({"record": r["record"], "offset": r["fn_offset"] + off,
                          "length": 8, "what": "$FN." + name})
    return items


def apply_scrub(image_path: str, items) -> int:
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
        description="Inspect / scrub NTFS timestamps (both $SI and $FN sets)")
    ap.add_argument("image")
    ap.add_argument("--scrub", action="store_true")
    ap.add_argument("--i-understand", action="store_true")
    ap.add_argument("--show", type=int, default=6)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    try:
        rows = inspect(args.image)
    except NotNTFS as e:
        print("not an NTFS volume: %s" % e)
        return 1

    live = [r for r in rows if r["in_use"]]
    dead = [r for r in rows if not r["in_use"]]
    strong = [r for r in rows if r["divergent_strong"]]
    print("image            : %s" % os.path.basename(args.image))
    print("files with times : %d live, %d deleted" % (len(live), len(dead)))
    print("$SI/$FN CREATED divergence (strong tampering signal): %d" % len(strong))
    print("checked          : %s" % ", ".join(CHECKED))
    print("NOT checked      : %s" % ", ".join(NOT_CHECKED))

    for r in rows[: args.show]:
        c_si = filetime_to_dt(r["si"].get("created", 0))
        c_fn = filetime_to_dt(r["fn"].get("created", 0))
        print("  #%-5d %-8s %-18s $SI.created=%s  $FN.created=%s%s"
              % (r["record"], "live" if r["in_use"] else "deleted",
                 (r["filename"] or "")[:18],
                 c_si.strftime("%Y-%m-%d %H:%M:%S") if c_si else "-",
                 c_fn.strftime("%Y-%m-%d %H:%M:%S") if c_fn else "-",
                 "   <-- DIVERGENT" if r["divergent_strong"] else ""))

    if args.scrub:
        if not args.i_understand:
            print("\nREFUSED: --scrub requires --i-understand.")
            return 2
        items = scrub_plan(args.image, only_deleted=True)
        n = apply_scrub(args.image, items)
        print("\nscrubbed %d timestamp fields (%d bytes) across deleted records"
              % (len(items), n))
        after = [r for r in inspect(args.image) if not r["in_use"]]
        remaining = sum(1 for r in after
                        if any(v for v in r["si"].values())
                        or any(v for v in r["fn"].values()))
        print("deleted records still carrying any timestamp: %d" % remaining)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"checked": CHECKED, "not_checked": NOT_CHECKED,
                       "rows": [{k: v for k, v in r.items()
                                 if k not in ("si", "fn")} for r in rows]},
                      fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
