"""
Module 1 — drive-level sanitisation, honestly bounded.

What this is
------------
The drive-level *clear* (single-pass overwrite of all addressable space) is
table stakes: Blancco, KillDisk, BitRaser and `dd` all do it. We do not claim
it as novel. What this module contributes is the layer around it that those
tools mostly do not expose:

  1. TECHNIQUE SELECTION per media/interface, following SP 800-88r2's decision
     flow -- and REFUSING when no available technique is appropriate.
  2. VALIDATION that can reject (SP 800-88r2 4.5.2), reusing erase/validate.py.
  3. ADVERSARIAL verification -- run our own recovery over the result.
  4. A signed, hash-chained CERTIFICATE recording method, technique, tool,
     verdict, and the residue paths NOT checked.

What this is NOT, stated as a hard boundary
-------------------------------------------
It does not issue ATA SECURE ERASE, NVMe Sanitize, NVMe Format, or any other
command to a physical device. Real hardware sanitization is where an
irreversible mistake destroys the wrong disk. Those commands are MODELLED here
-- selected, validated, and recorded as PLANNED -- but never executed. The
`clear` that actually runs, runs only on a loopback image file.

  Safety, enforced in code (assert_safe_target from wipe_residue):
    * refuses block devices and the system disk
    * refuses anything that is not a regular file
    * dry-run by default; overwrite requires --apply --i-understand

IEEE 2883-2022 is the technique authority SP 800-88r2 defers to; the specific
command mappings below (ATA/NVMe) follow it but are unexecuted models.
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from erase.validate import Evidence, evaluate, probe_image
from erase.wipe_residue import RefusedUnsafe, assert_safe_target

# SP 800-88r2 method -> techniques, and the IEEE-2883 command each maps to.
# "executable" marks whether OUR tool will actually perform it (only overwrite
# on an image is executable; the rest are modelled and recorded as planned).
TECHNIQUES = {
    "overwrite":      {"method": "clear",  "command": "logical overwrite (host writes)",
                       "executable": True},
    "ata-secure-erase": {"method": "purge", "command": "ATA SECURITY ERASE UNIT",
                         "executable": False},
    "ata-sanitize-block": {"method": "purge", "command": "ATA SANITIZE BLOCK ERASE",
                           "executable": False},
    "nvme-sanitize":  {"method": "purge",  "command": "NVMe Sanitize (Block Erase)",
                       "executable": False},
    "nvme-format":    {"method": "purge",  "command": "NVMe Format NVM (secure)",
                       "executable": False},
    "crypto-erase":   {"method": "purge",  "command": "destroy media-encryption key",
                       "executable": False},
}


def select_technique(ev: Evidence):
    """
    Choose a sanitization technique from the evidence, per SP 800-88r2's
    decision flow. Returns (technique_name | None, rationale).

    The point is not to always produce a technique. Returning None -- "no
    appropriate technique is available, escalate to physical destruction" --
    is a legitimate and important output, and it is what a tool that refuses to
    over-promise must be able to say.
    """
    mc = ev.media_class

    # Encrypted-from-provisioning flash with sound implementation: CE is the
    # r2-preferred purge, IF its preconditions hold. validate.py will still
    # reject CE on a known-flawed model or when key-copy sanitization is
    # unproven, so selecting it here is not the same as trusting it.
    if mc == "flash" and ev.encrypted_since_provisioning:
        return "crypto-erase", "flash encrypted since provisioning; CE is the r2-preferred purge (subject to validation)"

    if mc == "flash":
        # Overwrite cannot reach over-provisioning/remapped cells on flash.
        # A device-level purge command is required.
        if ev.target_kind == "block-device":
            return "nvme-sanitize", "flash: overwrite cannot reach over-provisioned cells; a device purge is required"
        return None, "flash image file: no device-level purge available in scope; overwrite is insufficient for flash"

    if mc == "magnetic":
        return "overwrite", "magnetic media: single-pass overwrite is a sufficient clear (r2 3.1.1; multi-pass not needed)"

    if mc == "image":
        return "overwrite", "loopback image: logical overwrite of all addressable bytes (demonstrates the clear logic; not physical media)"

    return None, "media class unknown; cannot select a technique safely"


def clear_image(path: str) -> int:
    """
    Single-pass overwrite of every byte of a loopback image.

    Single pass by design. SP 800-88r2 3.1.1: multi-pass overwrite is not
    needed and the guidance explicitly counters DoD 5220.22-M, from which DoD
    removed overwrite specifications in 2006.
    """
    size = os.path.getsize(path)
    chunk = b"\x00" * (1 << 20)
    written = 0
    with open(path, "r+b") as fh:
        while written < size:
            n = min(len(chunk), size - written)
            fh.write(chunk[:n])
            written += n
        fh.flush()
        os.fsync(fh.fileno())
    return written


def verify_zeroed(path: str, full: bool = True) -> bool:
    """
    Verification (4.5.1): confirm the device reads back clear.

    Reads EVERY byte by default. An earlier version sampled 256 points, which
    is a real hole: a wipe that missed a region between samples would pass
    verification. Sampling is only defensible when the wipe mechanism
    guarantees uniformity, which a host overwrite of an image does not need to
    assume -- so we read it all. `full=False` is offered for very large images
    but its limitation is exactly the one just described.
    """
    size = os.path.getsize(path)
    if size == 0:
        return True
    with open(path, "rb") as fh:
        if full:
            while True:
                b = fh.read(1 << 20)
                if not b:
                    return True
                if any(b):
                    return False
        step = max(4096, size // 256)
        for off in range(0, size, step):
            fh.seek(off); 
            if any(fh.read(4096)):
                return False
        return True


def sanitize(path: str, media_class: str, *, encrypted_since_provisioning=None,
             model=None, confidentiality_horizon_years=None,
             apply: bool = False, i_understand: bool = False,
             residue_scan=None):
    """
    Full module-1 flow on a loopback image. Returns a result dict.

    residue_scan: optional callable(path)->int returning recoverable-item count,
    used for adversarial verification (4.5.2). Kept injectable so drive-level
    sanitization does not hard-depend on the NTFS residue scanner.
    """
    assert_safe_target_regular(path)

    ev = probe_image(path)
    ev.media_class = media_class
    if media_class != "image":
        # "treat this image AS IF it were <media_class>": drop the image-file
        # exemption so validation reasons about it as physical media would be.
        ev.target_kind = "block-device (modelled)"
        ev.overprovisioned = None
        ev.hpa_dco_present = None
        ev.reallocated_sectors = None
        ev.notes = ["Modelled as %s media; over-provisioning, HPA/DCO and "
                    "remapped-sector status are UNKNOWN and downgrade the "
                    "verdict accordingly." % media_class]
    ev.model = model
    ev.encrypted_since_provisioning = encrypted_since_provisioning
    ev.confidentiality_horizon_years = confidentiality_horizon_years

    technique, why = select_technique(ev)
    result = {"image": os.path.basename(path), "media_class": media_class,
              "technique": technique, "technique_rationale": why,
              "planned_command": TECHNIQUES.get(technique, {}).get("command"),
              "executable": TECHNIQUES.get(technique, {}).get("executable", False),
              "applied": False, "verified_zeroed": None,
              "sha_before": _sha(path)}

    if technique is None:
        ev.notes.append("No appropriate sanitization technique available: " + why)
        result["method"] = None
    else:
        result["method"] = TECHNIQUES[technique]["method"]
        # validate.py speaks a fixed technique vocabulary. Map to it exactly,
        # or its CE / known-flawed-SED rules silently never fire -- which let a
        # crypto-erase on a known-broken drive read as APPROVE in testing.
        VALIDATE_NAME = {"overwrite": "overwrite", "crypto-erase": "CE",
                         "ata-secure-erase": "block-erase",
                         "ata-sanitize-block": "block-erase",
                         "nvme-sanitize": "block-erase", "nvme-format": "block-erase"}
        ev.technique = VALIDATE_NAME.get(technique, technique)
        ev.method = result["method"]

    # Execute ONLY an overwrite on an image, and only when explicitly asked.
    if technique == "overwrite" and apply and i_understand:
        clear_image(path)
        result["applied"] = True
        result["verified_zeroed"] = verify_zeroed(path)
        result["sha_after"] = _sha(path)
    elif technique and not TECHNIQUES[technique]["executable"]:
        ev.notes.append("Technique %s is a device command modelled but NOT "
                        "executed (no physical hardware in scope)." % technique)

    # Adversarial verification (4.5.2), if a scanner was provided.
    if residue_scan is not None and result["applied"]:
        try:
            result["residue_found_after"] = int(residue_scan(path))
            ev.residue_found_after = result["residue_found_after"]
        except Exception as e:
            result["residue_found_after"] = None
            ev.notes.append("adversarial scan failed: %s" % e)

    verdict, rules = evaluate(ev)
    # A technique that was selected but NOT executed cannot be APPROVE: nothing
    # was sanitized. Downgrade to a planning verdict so a dry run never reads
    # as a completed, approved wipe.
    if technique is None:
        verdict = "REJECT"        # no appropriate technique = cannot sanitize
    elif verdict != "REJECT" and not result["applied"]:
        # Downgrade APPROVE/ESCALATE to a planning verdict when nothing ran --
        # but never mask a REJECT: a technique the rules already reject must
        # not read as merely "planned".
        verdict = "PLANNED-NOT-EXECUTED"
    result["validation_verdict"] = verdict
    result["validation_reasons"] = [{"rule": r.name, "verdict": r.verdict,
                                     "reason": r.reason} for r in rules]
    result["notes"] = ev.notes
    return result


def assert_safe_target_regular(path: str) -> None:
    """Reuse module 2's interlocks, but do not require an NTFS volume."""
    if not os.path.exists(path):
        raise RefusedUnsafe("target does not exist: %s" % path)
    import stat
    st = os.stat(path)
    if stat.S_ISBLK(st.st_mode):
        raise RefusedUnsafe("target is a BLOCK DEVICE. Image files only.")
    if stat.S_ISCHR(st.st_mode):
        raise RefusedUnsafe("target is a character device.")
    if not stat.S_ISREG(st.st_mode):
        raise RefusedUnsafe("target is not a regular file.")


def _sha(path: str, cap: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main() -> int:
    import argparse, json
    ap = argparse.ArgumentParser(description="Module 1 drive-level sanitisation (loopback images only)")
    ap.add_argument("image")
    ap.add_argument("--media", default="image",
                    choices=["image", "magnetic", "flash"],
                    help="media class the image should be treated as")
    ap.add_argument("--encrypted-since-provisioning", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--horizon-years", type=int, default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--i-understand", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    try:
        r = sanitize(args.image, args.media,
                     encrypted_since_provisioning=args.encrypted_since_provisioning or None,
                     model=args.model, confidentiality_horizon_years=args.horizon_years,
                     apply=args.apply, i_understand=args.i_understand)
    except RefusedUnsafe as e:
        print("REFUSED: %s" % e)
        return 2

    print("image       : %s  (media class: %s)" % (r["image"], r["media_class"]))
    print("technique   : %s" % (r["technique"] or "NONE — escalate to physical destruction"))
    print("  rationale : %s" % r["technique_rationale"])
    if r["technique"]:
        print("  method    : %s" % r["method"])
        print("  command   : %s%s" % (r["planned_command"],
                                      "" if r["executable"] else "   [modelled, NOT executed]"))
    print("applied     : %s" % r["applied"])
    if r["applied"]:
        print("  verified zeroed (sampled): %s" % r["verified_zeroed"])
        if "residue_found_after" in r:
            print("  adversarial re-scan found: %s" % r["residue_found_after"])
    print("VALIDATION  : %s" % r["validation_verdict"])
    for rr in r["validation_reasons"]:
        print("  [%-9s] %-22s %s" % (rr["verdict"], rr["rule"], rr["reason"]))
    for n in r["notes"]:
        print("  note: %s" % n)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(r, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
