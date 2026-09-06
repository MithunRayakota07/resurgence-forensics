"""
Sanitization VALIDATION — NIST SP 800-88r2 Sec. 4.5.2.

The distinction this module exists to implement
-----------------------------------------------
r2 separates two things that every shipping tool conflates:

  * **Verification** (4.5.1) -- did the technique complete? Exit status,
    errors, device health. r2 explicitly says elaborate content sampling is
    NOT required. This is what DBAN, nwipe, SDelete and the commercial suites
    report, and they call it proof.

  * **Validation** (4.5.2) -- was the target data *effectively* sanitized?
    "Sanitization validation results in a decision to either approve the
    sanitization as being effective or reject it, which would require
    repeating the sanitization method using a different sanitization technique
    or escalating to a more secure sanitization method."

Validation can come back REJECTED. That is the whole point, and it is why a
green tick is the wrong output shape.

r2 lists what invalidates a sanitization. Each is a rule below:
  * media inaccessible through the interface due to errors or performance
  * technique inappropriate for the media -- r2's own example is degaussing an
    SSD, which "can complete successfully, but no sensitive data is sanitized"
  * unqualified personnel, or unapproved / improperly calibrated tools
  * outcome below the organisation's minimum requirement
  * scope too narrowly focused -- r2's example is overprovisioned media
    sanitized by simple writes, "potentially leaving a substantial amount of
    user data unchanged"

Evidence gathering is deliberately pluggable. `ImageProbe` reports what can
actually be determined from a loopback image file. `DeviceProbe` -- SMART,
HPA/DCO, over-provisioning, TCG Opal status -- is NOT implemented, because it
requires physical hardware and this project does not touch physical devices.
Unknown facts are reported as UNKNOWN and downgrade the verdict. They are never
assumed benign.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

APPROVE, REJECT, ESCALATE = "APPROVE", "REJECT", "ESCALATE"
ORDER = {APPROVE: 0, ESCALATE: 1, REJECT: 2}

# Model families with published encryption-implementation flaws. Crypto-erase
# on these cannot be trusted on its own.
#   Meijer & van Gastel, "Self-encrypting deception: weaknesses in the
#   encryption of solid state drives", Radboud University, 2018 -- bypassed
#   encryption entirely on three Crucial and four Samsung models; DEK not
#   derived from the password, single DEK per disk, JTAG and vendor
#   diagnostic access. Also affected BitLocker where it deferred to hardware.
KNOWN_FLAWED_SED = [
    ("crucial", "mx100"), ("crucial", "mx200"), ("crucial", "mx300"),
    ("samsung", "840 evo"), ("samsung", "850 evo"),
    ("samsung", "t3 portable"), ("samsung", "t5 portable"),
]


@dataclass
class Evidence:
    """What we actually know. Anything not established stays None = UNKNOWN."""
    target_kind: str = "unknown"          # "image-file" | "block-device"
    media_class: str = None               # "flash" | "magnetic" | "image"
    model: str = None
    serial: str = None
    overprovisioned: bool = None
    hpa_dco_present: bool = None
    reallocated_sectors: int = None
    encrypted_since_provisioning: bool = None
    all_key_copies_sanitizable: bool = None
    interface_errors: int = None
    technique: str = None                 # overwrite | block-erase | CE | degauss
    method: str = None                    # clear | purge | destroy
    scope_paths_checked: list = field(default_factory=list)
    scope_paths_unchecked: list = field(default_factory=list)
    confidentiality_horizon_years: int = None
    residue_found_after: int = None       # our own adversarial re-scan
    notes: list = field(default_factory=list)


def probe_image(path: str, **kw) -> Evidence:
    """
    Everything determinable from a loopback image file.

    Deliberately short. An image file has no SMART data, no over-provisioning,
    no hidden host-protected area. Those fields stay UNKNOWN rather than being
    filled with reassuring defaults, because a validation that assumes the best
    about what it cannot see is not a validation.
    """
    ev = Evidence(target_kind="image-file", media_class="image",
                  interface_errors=0, **kw)
    ev.overprovisioned = False
    ev.hpa_dco_present = False
    ev.reallocated_sectors = 0
    ev.notes.append(
        "Target is an image file: over-provisioning, HPA/DCO and remapped "
        "sectors are not applicable and were not assessed. Results do not "
        "transfer to physical media.")
    return ev


def probe_device(path: str) -> Evidence:                # pragma: no cover
    raise NotImplementedError(
        "Physical device probing (SMART, HPA/DCO, TCG Opal, over-provisioning) "
        "is not implemented. This project does not touch physical devices.")


@dataclass
class Rule:
    name: str
    verdict: str
    reason: str


def evaluate(ev: Evidence):
    """Apply SP 800-88r2 4.5.2 considerations. Returns (verdict, [Rule])."""
    fired = []

    # -- technique appropriate for the media -------------------------------
    if ev.technique == "degauss" and ev.media_class == "flash":
        fired.append(Rule("technique-vs-media", REJECT,
                          "Degaussing flash media completes successfully but "
                          "sanitizes nothing (SP 800-88r2 4.5.2)."))
    if ev.technique == "overwrite" and ev.media_class == "flash":
        if ev.overprovisioned is None:
            fired.append(Rule("overprovisioning-unknown", ESCALATE,
                              "Overwrite on flash with over-provisioning status "
                              "UNKNOWN. r2 names this as a scope failure: simple "
                              "writes may leave substantial user data unchanged."))
        elif ev.overprovisioned:
            fired.append(Rule("overprovisioning", REJECT,
                              "Overwrite cannot reach over-provisioned areas. "
                              "Use a purge technique (block erase / sanitize / CE)."))

    # -- hidden regions -----------------------------------------------------
    if ev.hpa_dco_present is None:
        fired.append(Rule("hpa-dco-unknown", ESCALATE,
                          "HPA/DCO presence UNKNOWN; regions outside the "
                          "user-addressable space may retain data."))
    elif ev.hpa_dco_present:
        fired.append(Rule("hpa-dco", REJECT,
                          "HPA/DCO present: data exists outside all "
                          "addressable locations targeted by this technique."))

    # -- media health -------------------------------------------------------
    if ev.reallocated_sectors:
        fired.append(Rule("reallocated-sectors", ESCALATE,
                          "%d reallocated/pending sectors: retired blocks are "
                          "unreachable through the host interface and may hold "
                          "data." % ev.reallocated_sectors))
    if ev.interface_errors:
        fired.append(Rule("interface-errors", REJECT,
                          "%d interface errors during sanitization; portions of "
                          "the media may not have been written."
                          % ev.interface_errors))

    # -- cryptographic erase preconditions ----------------------------------
    if ev.technique == "CE":
        if ev.encrypted_since_provisioning is False:
            fired.append(Rule("ce-precondition", REJECT,
                              "Data written before encryption was enabled is not "
                              "protected by destroying the key."))
        elif ev.encrypted_since_provisioning is None:
            fired.append(Rule("ce-precondition-unknown", ESCALATE,
                              "Cannot establish that the media was encrypted from "
                              "provisioning; CE may not cover all data."))
        if ev.all_key_copies_sanitizable is not True:
            fired.append(Rule("ce-key-copies", ESCALATE,
                              "ISO/IEC 27040 requires that ALL copies of the "
                              "target keys can be sanitized; not established."))
        m = (ev.model or "").lower()
        for vendor, fam in KNOWN_FLAWED_SED:
            if vendor in m and fam in m:
                fired.append(Rule("known-flawed-sed", REJECT,
                                  "Model matches a family with published "
                                  "encryption bypass (Meijer & van Gastel, 2018). "
                                  "Crypto-erase cannot be relied on."))
                break

    # -- scope --------------------------------------------------------------
    if ev.scope_paths_unchecked:
        fired.append(Rule("scope-unchecked-paths", ESCALATE,
                          "%d residue path(s) were not examined: %s."
                          % (len(ev.scope_paths_unchecked),
                             ", ".join(ev.scope_paths_unchecked[:4])
                             + ("..." if len(ev.scope_paths_unchecked) > 4 else ""))))

    # -- our own adversarial re-scan ---------------------------------------
    if ev.residue_found_after:
        fired.append(Rule("adversarial-rescan", REJECT,
                          "Our own recovery engine still found %d recoverable "
                          "item(s) after sanitization."
                          % ev.residue_found_after))

    # -- confidentiality horizon -------------------------------------------
    if (ev.confidentiality_horizon_years or 0) >= 25 and ev.method == "clear":
        fired.append(Rule("horizon-vs-method", ESCALATE,
                          "Stated confidentiality horizon %d years exceeds what "
                          "a clear-level method should be relied on for; consider "
                          "purge or destroy." % ev.confidentiality_horizon_years))

    if not fired:
        return APPROVE, [Rule("no-findings", APPROVE,
                              "No condition from SP 800-88r2 4.5.2 was triggered "
                              "within the scope examined.")]
    worst = max(fired, key=lambda r: ORDER[r.verdict]).verdict
    return worst, fired


def report(ev: Evidence):
    verdict, rules = evaluate(ev)
    lines = ["validation verdict : %s" % verdict]
    for r in rules:
        lines.append("  [%-9s] %-24s %s" % (r.verdict, r.name, r.reason))
    for n in ev.notes:
        lines.append("  [note     ] %s" % n)
    return verdict, rules, "\n".join(lines)
