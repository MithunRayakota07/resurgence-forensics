"""
End-to-end: does the carver still recover the benchmark files byte-exact?

Marked `slow` because a carve takes tens of seconds. Run the fast suite with
`pytest -m "not slow"`; CI runs everything.

Each image is carved ONCE per session and the result reused -- carving
hard.img separately for every assertion tripled the runtime for no extra
coverage.

These skip themselves when corpus/images is absent, since it is gitignored.
Regenerate with `resurgence-gen-corpus`.

The uniform-prior half of the ablation is deliberately NOT here: the control
takes ~29 minutes because the search explodes without the prior, which is the
very result being demonstrated. It lives in the benchmark harness instead.
"""

from __future__ import annotations

import json

import pytest

from carve.carver import carve_image
from conftest import require_image

pytestmark = pytest.mark.slow

_CARVES = {}


def carved(name: str):
    """Carve an image once per session, then hand back the cached result."""
    if name not in _CARVES:
        image = require_image(name)
        with open(image.replace(".img", ".manifest.json")) as fh:
            manifest = json.load(fh)
        _CARVES[name] = (
            carve_image(image, prior_name="locality", beam_width=8,
                        collect_trace=False),
            manifest,
        )
    return _CARVES[name]


@pytest.mark.parametrize("name", ["hard.img", "easy.img"])
def test_recovers_every_file_byte_exact(name):
    """
    The headline claim. Compares SHA-256 against the ground-truth manifest --
    legitimate here because this is evaluation, not runtime. A carver that
    consulted the original at runtime would be measuring nothing.
    """
    result, manifest = carved(name)
    expected = {f["sha256"] for f in manifest["files"]}
    recovered = {f["sha256"] for f in result["files"] if f["ok"]}

    missing = expected - recovered
    assert not missing, "%d of %d file(s) not recovered byte-exact" % (
        len(missing), len(expected))


def test_hard_image_finds_the_out_of_order_path():
    """
    hard.img is the case no other tool solves: three fragments with a BACKWARD
    jump. A wrong assembly that happened to hash correctly is impossible, but a
    right hash reached with the wrong fragment count would still be worth
    catching, so the cluster order is checked too.
    """
    result, manifest = carved("hard.img")
    truth = manifest["files"][0]

    ok = [f for f in result["files"] if f["ok"]]
    assert len(ok) == 1
    got = ok[0]

    assert got["sha256"] == truth["sha256"]
    assert got["clusters"] == truth["cluster_order"]
    assert got["n_fragments"] == truth["n_fragments"] == 3
    assert got["mcus"] == got["total_mcus"]

    # the backward jump itself: some cluster is followed by an earlier one
    order = got["clusters"]
    assert any(b < a for a, b in zip(order, order[1:])), \
        "no backward jump in the recovered path; hard.img is not being solved"


def test_confidence_is_reported_as_uncalibrated():
    """
    Project rule: the confidence number is not a probability until it is
    calibrated, and every surface that shows it must say so. If this flag ever
    flips to True, the calibration work has to have actually happened.
    """
    result, _ = carved("hard.img")
    assert result["files"]
    for f in result["files"]:
        assert f["confidence_calibrated"] is False
        assert 0.0 <= f["confidence"] <= 1.0


def test_prior_is_flagged_as_hand_set_not_measured():
    """
    LocalityPrior's numbers are placeholders, not fitted to real filesystems.
    The result carries that admission so a report cannot quietly present them
    as measured.
    """
    result, _ = carved("hard.img")
    assert result["prior"] == "locality"
    assert result["prior_is_measured"] is False
