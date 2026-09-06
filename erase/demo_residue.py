"""
End-to-end residue demonstration.

    1. A conventional tool deletes files and wipes free space.
    2. We recover the "deleted" content anyway.
    3. We erase it properly.
    4. We look again and find nothing.
    5. A certificate records what was checked -- and what was not.

Every number printed is measured from the images at run time. Nothing here is
narrated from a script.

Honest naming: the recovery in step 2 is a direct read of resident $DATA out
of unallocated MFT records, not fragmented-file carving. It is "our recovery
engine" only in the sense that it is our code finding data a competitor missed.
The JPEG carver would be the engine for residue that happens to be a
fragmented image; that case is not demonstrated here.
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from erase.certificate import CertificateStore, build_body
from erase.residue import CHECKED_PATHS, UNCHECKED_PATHS, summarise
from erase.validate import probe_image, report as validation_report
from erase.wipe_residue import apply_plan, assert_safe_target, plan


def rule(t=""):
    print("\n" + "=" * 74)
    if t:
        print(t)
        print("=" * 74)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="corpus/ntfs_residue")
    ap.add_argument("--out", default="bench/out")
    args = ap.parse_args()

    after_competitor = os.path.join(args.dir, "v3_freespace_wiped.img")
    ours = os.path.join(args.dir, "v4_our_erase.img")
    if not os.path.exists(after_competitor):
        print("missing %s -- run corpus/generate/ntfs_residue.sh first" % after_competitor)
        return 1
    os.makedirs(args.out, exist_ok=True)

    rule("STEP 1 - the volume AFTER a conventional secure-delete workflow")
    print("Files were deleted normally, then free space was overwritten -- the")
    print("step tools advertise as making deleted files unrecoverable.")
    before = summarise(after_competitor)
    print("\n  recoverable deleted files : %d" % before["recoverable_resident_files"])
    print("  recoverable content       : %d bytes" % before["recoverable_bytes"])
    print("  orphan filenames          : %d" % before["orphan_filenames"])

    rule("STEP 2 - what we recover from it")
    for f in before["findings"][:5]:
        print("  MFT #%-5d %5d B  %-16s %s"
              % (f["mft_record"], f["length"], f["filename"] or "(name gone)",
                 f["preview"].replace("\n", " ")[:44]))
    if len(before["findings"]) > 5:
        print("  ... and %d more" % (len(before["findings"]) - 5))
    print("\n  Why the wipe missed them: NTFS stores a small file's data INSIDE")
    print("  its MFT record. No cluster is ever allocated, so a tool that")
    print("  overwrites unallocated CLUSTERS never touches it.")

    rule("STEP 3 - our erasure")
    shutil.copyfile(after_competitor, ours)
    try:
        assert_safe_target(ours)
    except Exception as e:
        print("REFUSED: %s" % e)
        return 2
    items = plan(ours)
    total = sum(i["length"] for i in items)
    print("  plan: %d regions, %d bytes (single pass)" % (len(items), total))
    written = apply_plan(ours, items)
    print("  overwrote %d bytes" % written)

    rule("STEP 4 - look again")
    after = summarise(ours)
    print("  recoverable deleted files : %d" % after["recoverable_resident_files"])
    print("  recoverable content       : %d bytes" % after["recoverable_bytes"])
    print("  orphan filenames          : %d" % after["orphan_filenames"])

    ok = (after["recoverable_resident_files"] == 0 and after["orphan_filenames"] == 0)

    rule("STEP 5 - certificate")
    store = CertificateStore(os.path.join(args.out, "certificates.jsonl"))

    # The verdict comes from the SP 800-88r2 4.5.2 rule engine, not from
    # "did our own erase loop finish". An earlier version hard-coded APPROVE
    # when the re-scan came back clean, which contradicted the validator: we
    # left five residue paths unexamined, so the honest verdict is ESCALATE.
    ev = probe_image(ours, technique="overwrite", method="clear")
    ev.scope_paths_checked = list(CHECKED_PATHS)
    ev.scope_paths_unchecked = list(UNCHECKED_PATHS)
    ev.residue_found_after = (after["recoverable_resident_files"]
                              + after["orphan_filenames"])
    verdict, rules, vtext = validation_report(ev)
    reasons = ["[%s] %s" % (r.name, r.reason) for r in rules] + ev.notes
    print(vtext)
    print()

    body = build_body(
        media={"type": "loopback image (NTFS volume)",
               "path": os.path.basename(ours),
               "bytes": os.path.getsize(ours),
               "manufacturer": "n/a (image file)", "model": "n/a",
               "serial": "n/a", "property_number": "n/a",
               "source": "SUTRA test corpus"},
        operator={"name": "unattended demo run", "title": "n/a",
                  "organization": "SUTRA / SIH26149", "location": "local",
                  "contact": "n/a"},
        method="clear",
        technique="targeted overwrite of resident attribute content",
        tool="SUTRA erase/wipe_residue.py", tool_version="0.1-phase0",
        verification={"regions_planned": len(items), "bytes_written": written,
                      "errors": 0,
                      "method": "re-scan of all unallocated MFT records"},
        validation_verdict=verdict, validation_reasons=reasons,
        paths_checked=CHECKED_PATHS, paths_not_checked=UNCHECKED_PATHS,
        evidence={"before": {k: before[k] for k in
                             ("recoverable_resident_files", "recoverable_bytes",
                              "orphan_filenames")},
                  "after": {k: after[k] for k in
                            ("recoverable_resident_files", "recoverable_bytes",
                             "orphan_filenames")}},
    )
    rec = store.append(body)
    print("  certificate verdict : %s" % verdict)
    print("  record hash         : %s..." % rec["record_hash"][:32])
    print("  signature           : %s..." % rec.get("signature", "(unsigned)")[:32])
    good, problems = store.verify()
    print("  chain               : %s" % ("VERIFIED" if good else "BROKEN: %s" % problems))
    print("\n  Declared NOT checked: %s" % ", ".join(UNCHECKED_PATHS))
    print("  The certificate states its own scope. Absence of findings outside")
    print("  that scope is not evidence of absence.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
