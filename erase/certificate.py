"""
Certificate of Sanitization — NIST SP 800-88r2 Appendix C, plus what it omits.

Field set is taken directly from SP 800-88r2 Sec. 4.6, which states the
certificate should record at least: manufacturer, model, serial number,
organizationally assigned property number, media type, media source,
pre-sanitization categorization (optional), sanitization METHOD (clear / purge
/ destroy), sanitization TECHNIQUE (overwrite / block erase / CE / degauss),
tool including version, verification method, and for each person involved:
name, title, date, location, contact, signature.

Three things we add, and why
----------------------------
1. **The validation verdict, not just completion.** r2 Sec. 4.5 separates
   *verification* (did the technique complete?) from *validation* (was the
   target data effectively sanitized -- a decision that may REJECT). Existing
   tools report the former. The certificate carries both, and validation may
   say REJECT or ESCALATE.

2. **An explicit list of residue paths NOT checked.** A certificate that
   implies coverage it does not have is worse than none. Ours enumerates what
   was examined and what was not, so the reader can price the gap. Measured on
   our own scenario: after erasing MFT-resident residue, 705 deleted filenames
   still survived in the $I30 directory index -- a path we declare unchecked.

3. **Ed25519 signature over a canonical serialisation, in a hash chain.**
   Each certificate carries the hash of the previous one, so removing or
   reordering records is detectable.

Deliberately NOT blockchain. Blockchain solves agreement between mutually
distrusting parties; a sanitization certificate has exactly one issuer. A
signed hash chain gives tamper-evidence and ordering without the theatre.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.encoding import HexEncoder
    HAVE_NACL = True
except ImportError:                                     # pragma: no cover
    HAVE_NACL = False

METHODS = ("clear", "purge", "destroy")
VERDICTS = ("APPROVE", "REJECT", "ESCALATE")


def canonical(obj) -> bytes:
    """Deterministic serialisation. Signatures must not depend on key order."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


class CertificateStore:
    """Append-only, hash-chained, signed certificate log."""

    def __init__(self, path: str, key_path: str = None):
        self.path = path
        self.key_path = key_path or (os.path.splitext(path)[0] + ".key")
        self._key = None

    # -- key handling ------------------------------------------------------
    def key(self):
        if not HAVE_NACL:
            raise RuntimeError("PyNaCl is required for signing")
        if self._key is None:
            if os.path.exists(self.key_path):
                with open(self.key_path, "rb") as f:
                    self._key = SigningKey(f.read(), encoder=HexEncoder)
            else:
                self._key = SigningKey.generate()
                os.makedirs(os.path.dirname(os.path.abspath(self.key_path)) or ".",
                            exist_ok=True)
                with open(self.key_path, "wb") as f:
                    f.write(self._key.encode(encoder=HexEncoder))
                try:
                    os.chmod(self.key_path, 0o600)
                except Exception:
                    pass                                # best effort on Windows
        return self._key

    def public_key_hex(self) -> str:
        return self.key().verify_key.encode(encoder=HexEncoder).decode()

    # -- chain -------------------------------------------------------------
    def entries(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def head_hash(self) -> str:
        e = self.entries()
        return e[-1]["record_hash"] if e else "0" * 64

    def append(self, body: dict) -> dict:
        body = dict(body)
        body["issued_utc"] = datetime.now(timezone.utc).isoformat()
        body["prev_hash"] = self.head_hash()
        digest = hashlib.sha256(canonical(body)).hexdigest()
        rec = {"body": body, "record_hash": digest}
        if HAVE_NACL:
            rec["signature"] = self.key().sign(bytes.fromhex(digest)).signature.hex()
            rec["public_key"] = self.public_key_hex()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def verify(self, expect_pubkey: str = None):
        """
        Re-check every hash, signature and chain link. Returns (ok, problems).

        `expect_pubkey` PINS the signer. Without it, verification falls back to
        the key embedded in each record -- and that is not a security property:
        an attacker who rewrites the whole log can re-sign every record with a
        key of their own and it will verify happily. The embedded key proves
        only that the records are self-consistent, not that we issued them.

        Callers holding the issuing key should always pass it. The CLI does.
        """
        problems = []
        prev = "0" * 64
        pinned = expect_pubkey
        if pinned is None and os.path.exists(self.key_path) and HAVE_NACL:
            pinned = self.public_key_hex()
        if pinned is None:
            problems.append("WARNING: no pinned public key; signatures prove "
                            "self-consistency only, not authorship")

        for i, rec in enumerate(self.entries()):
            body = rec["body"]
            if body.get("prev_hash") != prev:
                problems.append("record %d: chain break (prev_hash mismatch)" % i)
            digest = hashlib.sha256(canonical(body)).hexdigest()
            if digest != rec["record_hash"]:
                problems.append("record %d: body altered (hash mismatch)" % i)
            if pinned and rec.get("public_key") and rec["public_key"] != pinned:
                problems.append("record %d: signed by an unexpected key" % i)
            if HAVE_NACL and "signature" in rec:
                key = pinned or rec.get("public_key")
                try:
                    VerifyKey(bytes.fromhex(key)).verify(
                        bytes.fromhex(rec["record_hash"]),
                        bytes.fromhex(rec["signature"]))
                except Exception as e:
                    problems.append("record %d: bad signature (%s)" % (i, e))
            prev = rec["record_hash"]
        return (not problems), problems


def build_body(*, media, operator, method, technique, tool, tool_version,
               verification, validation_verdict, validation_reasons,
               paths_checked, paths_not_checked, evidence) -> dict:
    if method not in METHODS:
        raise ValueError("method must be one of %s" % (METHODS,))
    if validation_verdict not in VERDICTS:
        raise ValueError("verdict must be one of %s" % (VERDICTS,))
    return {
        "standard": "NIST SP 800-88r2 (September 2025), Appendix C",
        "technique_authority": "IEEE 2883-2022 (referenced by SP 800-88r2)",
        "media": media,
        "operator": operator,
        "sanitization": {
            "method": method,
            "technique": technique,
            "tool": tool,
            "tool_version": tool_version,
            "passes": 1,
            "passes_rationale": ("single pass; SP 800-88r2 3.1.1 states multi-pass "
                                 "overwrite is not needed and counters DoD 5220.22-M, "
                                 "from which DoD removed overwrite specifications "
                                 "in 2006"),
        },
        "verification": verification,
        "validation": {
            "verdict": validation_verdict,
            "reasons": validation_reasons,
            "basis": "SP 800-88r2 4.5.2 -- validation is a decision that may reject",
        },
        "coverage": {
            "paths_checked": paths_checked,
            "paths_NOT_checked": paths_not_checked,
            "caveat": ("Residue paths not listed as checked were not examined. "
                       "Absence of findings is not evidence of absence there."),
        },
        "evidence": evidence,
    }
