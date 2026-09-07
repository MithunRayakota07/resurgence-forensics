"""
The certificate log: hash chain, signatures, and tamper-evidence.

The claim this file makes is that removing, reordering or editing a
sanitization record is detectable. That claim is only worth anything if it is
tested, so each way of tampering gets its own test.
"""

from __future__ import annotations

import json

import pytest

from erase import certificate as C
from erase.certificate import CertificateStore, build_body, canonical


def _body(**over):
    kw = dict(
        media="loopback image evidence.img",
        operator="test operator",
        method="clear",
        technique="overwrite",
        tool="SUTRA",
        tool_version="0.1.0",
        verification="full read-back",
        validation_verdict="APPROVE",
        validation_reasons=[],
        paths_checked=["MFT-resident $DATA"],
        paths_not_checked=["$I30 directory index"],
        evidence={"sha_before": "aa", "sha_after": "bb"},
    )
    kw.update(over)
    return build_body(**kw)


@pytest.fixture
def store(tmp_path):
    return CertificateStore(str(tmp_path / "certs.jsonl"))


# --------------------------------------------------------------------------
# build_body validates its vocabulary
# --------------------------------------------------------------------------

def test_rejects_a_method_outside_the_standard():
    with pytest.raises(ValueError):
        _body(method="shred-it-really-hard")


def test_rejects_a_verdict_outside_the_standard():
    with pytest.raises(ValueError):
        _body(validation_verdict="PROBABLY_FINE")


def test_records_the_single_pass_rationale():
    """SP 800-88r2 3.1.1: multi-pass is not needed. The certificate has to say
    why one pass is correct, or a reader assumes we cut a corner."""
    b = _body()
    assert b["sanitization"]["passes"] == 1
    assert "800-88r2" in b["sanitization"]["passes_rationale"]


# --------------------------------------------------------------------------
# canonical serialisation
# --------------------------------------------------------------------------

def test_canonical_form_ignores_key_order():
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------

def test_first_record_chains_to_zero(store):
    rec = store.append(_body())
    assert rec["body"]["prev_hash"] == "0" * 64


def test_each_record_chains_to_the_previous(store):
    first = store.append(_body())
    second = store.append(_body(operator="second operator"))
    assert second["body"]["prev_hash"] == first["record_hash"]


def test_a_clean_chain_verifies(store):
    store.append(_body())
    store.append(_body(operator="second"))
    ok, problems = store.verify()
    assert ok, problems


# --------------------------------------------------------------------------
# tampering, one way per test
# --------------------------------------------------------------------------

def test_editing_a_record_body_is_detected(store):
    store.append(_body())
    store.append(_body(operator="second"))

    lines = open(store.path).read().splitlines()
    rec = json.loads(lines[0])
    rec["body"]["operator"] = "somebody else entirely"
    lines[0] = json.dumps(rec, sort_keys=True)
    open(store.path, "w").write("\n".join(lines) + "\n")

    ok, problems = store.verify()
    assert not ok
    assert any("altered" in p for p in problems)


def test_removing_a_record_is_detected(store):
    store.append(_body())
    store.append(_body(operator="second"))
    store.append(_body(operator="third"))

    lines = open(store.path).read().splitlines()
    del lines[1]
    open(store.path, "w").write("\n".join(lines) + "\n")

    ok, problems = store.verify()
    assert not ok
    assert any("chain break" in p for p in problems)


def test_reordering_records_is_detected(store):
    store.append(_body())
    store.append(_body(operator="second"))

    lines = open(store.path).read().splitlines()
    lines.reverse()
    open(store.path, "w").write("\n".join(lines) + "\n")

    ok, problems = store.verify()
    assert not ok
    assert any("chain break" in p for p in problems)


# --------------------------------------------------------------------------
# signatures
# --------------------------------------------------------------------------

def test_pynacl_is_actually_installed():
    """
    Signing degrades silently to unsigned if PyNaCl is missing, which would
    make "signed certificate" a false claim. It is a hard dependency and this
    test is what keeps it declared.
    """
    assert C.HAVE_NACL, "PyNaCl missing: certificates would be written UNSIGNED"


def test_records_are_signed(store):
    rec = store.append(_body())
    assert "signature" in rec
    assert rec["public_key"] == store.public_key_hex()


def test_verification_pins_the_signing_key(store):
    """
    An attacker who rewrites the whole log can re-sign every record with their
    own key, and unpinned verification accepts it. Pinning is the real check,
    so it has to actually reject a foreign key.
    """
    store.append(_body())
    other_key = "11" * 32
    ok, problems = store.verify(expect_pubkey=other_key)
    assert not ok
    assert any("unexpected key" in p for p in problems)


def test_unpinned_verification_warns_that_it_proves_little(tmp_path):
    s = CertificateStore(str(tmp_path / "c.jsonl"), key_path=str(tmp_path / "k.key"))
    s.append(_body())
    import os
    os.remove(s.key_path)                     # no key available to pin with
    ok, problems = s.verify()
    assert any("no pinned public key" in p for p in problems)
