"""
The JPEG entropy decoder is the hard constraint the whole carver rests on.

Every downstream claim -- beam search, benchmark table, confidence score --
is only as good as "does this decoder agree with libjpeg, and does it refuse
to say COMPLETE when it should not". These are the checks that used to live in
bench/test_decoder.py as a printing script; that script stays as the
human-readable diagnostic, and these run in CI.
"""

from __future__ import annotations

import random

import pytest

from carve.validators import jpeg as J


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------

def test_header_parses_geometry(photo_header):
    h = photo_header
    assert h.width == 800
    assert h.height == 600
    assert h.total_mcus > 0
    assert h.mcus_x > 0
    assert h.scan_start > 0


def test_header_rejects_non_jpeg():
    with pytest.raises(Exception):
        J.parse_header(b"this is not a jpeg, not even slightly", 0)


# --------------------------------------------------------------------------
# 1. a clean file decodes exactly
# --------------------------------------------------------------------------

def test_clean_jpeg_decodes_complete(photo_jpeg, photo_header):
    st = J.DecodeState.initial(photo_header, photo_header.scan_start)
    res = J.decode_mcus(photo_header, photo_jpeg, st)
    assert res.status == J.COMPLETE
    assert res.state.mcu_index == photo_header.total_mcus


# --------------------------------------------------------------------------
# 2. truncation must NEVER look complete
# --------------------------------------------------------------------------

@pytest.mark.parametrize("frac", [0.25, 0.5, 0.75, 0.9])
def test_truncated_jpeg_is_never_complete(photo_jpeg, photo_header, frac):
    cut = int(len(photo_jpeg) * frac)
    res = J.decode_mcus(photo_header, photo_jpeg[:cut],
                        J.DecodeState.initial(photo_header, photo_header.scan_start))
    assert res.status != J.COMPLETE
    assert res.state.mcu_index < photo_header.total_mcus


# --------------------------------------------------------------------------
# 3. foreign data must never complete, and Huffman alone must not be trusted
# --------------------------------------------------------------------------

def _splice_trials(photo_jpeg, other_jpeg, photo_header, n=40, seed=7):
    """Cut the true file mid-scan, then continue it correctly and foreignly."""
    rnd = random.Random(seed)
    correct_costs, foreign_costs, survived = [], [], []

    for _ in range(n):
        cut = rnd.randrange(int(len(photo_jpeg) * 0.3), int(len(photo_jpeg) * 0.7))
        dc = []
        pre = J.decode_mcus(photo_header, photo_jpeg[:cut],
                            J.DecodeState.initial(photo_header, photo_header.scan_start),
                            dc_out=dc)
        if pre.status != J.NEED_MORE or pre.state.mcu_index < photo_header.mcus_x * 2:
            continue
        lo = pre.state.mcu_index

        dc_ok = list(dc)
        r_ok = J.decode_mcus(photo_header, photo_jpeg, pre.state, max_mcus=60, dc_out=dc_ok)
        if r_ok.mcus_decoded > 0:
            correct_costs.append(
                J.dc_continuity_cost(dc_ok, photo_header.mcus_x, lo, lo + r_ok.mcus_decoded))

        osrc = rnd.randrange(2000, max(2001, len(other_jpeg) - 4096))
        dc_bad = list(dc)
        spliced = photo_jpeg[:cut] + other_jpeg[osrc:osrc + 4096]
        r_bad = J.decode_mcus(photo_header, spliced, pre.state, max_mcus=60, dc_out=dc_bad)
        survived.append((r_bad.status, r_bad.mcus_decoded))
        if r_bad.mcus_decoded > 0:
            foreign_costs.append(
                J.dc_continuity_cost(dc_bad, photo_header.mcus_x, lo, lo + r_bad.mcus_decoded))

    return correct_costs, foreign_costs, survived


def test_foreign_splice_never_reports_complete(photo_jpeg, other_jpeg, photo_header):
    _, _, survived = _splice_trials(photo_jpeg, other_jpeg, photo_header)
    assert survived, "no usable splice trials were produced"
    assert all(status != J.COMPLETE for status, _ in survived)


def test_huffman_validity_alone_is_a_weak_discriminator(photo_jpeg, other_jpeg, photo_header):
    """
    Regression guard on a design lesson, not a bug.

    CLAUDE.md section 6: foreign fragments decode as valid symbols for a long
    way, because JPEGs from the same encoder share the standard Huffman tables.
    If this ever starts failing because foreign data desyncs immediately, the
    photometric scoring may have become unnecessary -- which would be good
    news, but it must be noticed rather than silently assumed.
    """
    _, _, survived = _splice_trials(photo_jpeg, other_jpeg, photo_header)
    decoded = sorted(n for _, n in survived)
    median = decoded[len(decoded) // 2]
    assert median > 10, ("foreign data desynced far faster than recorded "
                         "(median %d MCUs); re-check section 6" % median)


def test_dc_continuity_separates_correct_from_foreign(photo_jpeg, other_jpeg, photo_header):
    """
    The load-bearing number. Beam search only works because a correct
    continuation is photometrically smoother than a foreign one. Recorded
    separation on photographic input is 3.4x; the carver's own self-test
    treats anything below 2x as "beam search will not work".
    """
    correct, foreign, _ = _splice_trials(photo_jpeg, other_jpeg, photo_header)
    assert correct and foreign
    correct.sort()
    foreign.sort()
    c_med = correct[len(correct) // 2]
    f_med = foreign[len(foreign) // 2]
    assert c_med > 0
    assert f_med >= c_med * 2.0, ("DC continuity separation collapsed to %.2fx "
                                  "(need >=2x)" % (f_med / c_med))


# --------------------------------------------------------------------------
# 4. plausible_scan_data -- the CFReDS fill precondition (section 5b)
# --------------------------------------------------------------------------

def test_fill_text_is_rejected():
    """NIST CFReDS inter-fragment fill is literally this ASCII string repeated.
    Missing it cost three failed fix attempts."""
    fill = (b"*** FILL TEXT BLOCK ***" * 200)[:4096]
    assert J.plausible_scan_data(fill) is False


def test_real_scan_data_is_accepted(photo_jpeg, photo_header):
    block = photo_jpeg[photo_header.scan_start:photo_header.scan_start + 4096]
    assert J.plausible_scan_data(block) is True


def test_pure_text_carrying_eoi_is_accepted():
    """A file's final block is part scan data, part whatever follows it, so it
    can read as almost pure text. It is identified by carrying EOI instead."""
    tail = b"A" * 2000 + J.EOI_BYTES + b"readme text follows here" * 40
    assert J.plausible_scan_data(tail) is True


def test_empty_block_is_rejected():
    assert J.plausible_scan_data(b"") is False
