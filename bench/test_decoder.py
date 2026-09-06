"""
Smoke test for the JPEG entropy decoder.

This runs BEFORE anything is built on top of the decoder, because every
later claim -- beam search, benchmark table, confidence score -- is
downstream of "does this decoder actually agree with libjpeg".

Checks:
  1. A clean JPEG decodes to exactly total_mcus MCUs with status COMPLETE.
  2. Truncating the file yields NEED_MORE, never a false COMPLETE.
  3. Splicing foreign bytes into the entropy stream yields DESYNC, and we
     record HOW FAST it desyncs -- that number is the whole carving signal.
"""

import io
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carve.validators import jpeg as J
from corpus.generate.synthetic import encode_jpeg, make_photo


def make_test_jpeg(w=800, h=600, quality=88, seed=1) -> bytes:
    """Use the real corpus photo: DC continuity only exists on natural images."""
    return encode_jpeg(make_photo(w, h, seed=seed), quality=quality)


def main() -> int:
    fails = []
    data = make_test_jpeg()
    print("test JPEG: %d bytes" % len(data))

    # ---- 1. clean decode -------------------------------------------------
    hdr = J.parse_header(data, 0)
    print("header    : %s" % hdr.describe())
    st = J.DecodeState.initial(hdr, hdr.scan_start)
    res = J.decode_mcus(hdr, data, st)
    print("clean     : status=%s mcus=%d/%d detail=%s"
          % (res.status, res.state.mcu_index, hdr.total_mcus, res.detail))
    if res.status != J.COMPLETE:
        fails.append("clean JPEG did not decode to COMPLETE (got %s)" % res.status)
    if res.state.mcu_index != hdr.total_mcus:
        fails.append("clean JPEG MCU count %d != expected %d"
                     % (res.state.mcu_index, hdr.total_mcus))

    # ---- 2. truncation must never look COMPLETE --------------------------
    bad_trunc = 0
    for frac in (0.25, 0.5, 0.75, 0.9):
        cut = int(len(data) * frac)
        r = J.decode_mcus(hdr, data[:cut], J.DecodeState.initial(hdr, hdr.scan_start))
        if r.status == J.COMPLETE:
            bad_trunc += 1
        print("trunc %3d%%: status=%-9s mcus=%4d/%d"
              % (int(frac * 100), r.status, r.state.mcu_index, hdr.total_mcus))
    if bad_trunc:
        fails.append("%d truncated files falsely reported COMPLETE" % bad_trunc)

    # ---- 3. correct vs foreign continuation: DC continuity separation ----
    # Huffman validity alone is NOT enough: two JPEGs from the same encoder
    # share the standard Huffman tables, so a foreign fragment decodes as
    # perfectly valid symbols. The discriminator is photometric -- per-MCU
    # luma DC against the row above. This measures the separation, which is
    # the number the whole beam search depends on.
    other = make_test_jpeg(seed=99)
    rnd = random.Random(7)
    correct_costs, foreign_costs, survived = [], [], []

    for _ in range(40):
        cut = rnd.randrange(int(len(data) * 0.3), int(len(data) * 0.7))

        # decode the true prefix, capturing DC trace and resumable state
        dc = []
        pre = J.decode_mcus(hdr, data[:cut], J.DecodeState.initial(hdr, hdr.scan_start),
                            dc_out=dc)
        if pre.status != J.NEED_MORE or pre.state.mcu_index < hdr.mcus_x * 2:
            continue
        lo = pre.state.mcu_index

        # (a) the CORRECT continuation
        dc_ok = list(dc)
        r_ok = J.decode_mcus(hdr, data, pre.state, max_mcus=60, dc_out=dc_ok)
        if r_ok.mcus_decoded > 0:
            correct_costs.append(
                J.dc_continuity_cost(dc_ok, hdr.mcus_x, lo, lo + r_ok.mcus_decoded))

        # (b) a FOREIGN continuation
        osrc = rnd.randrange(2000, max(2001, len(other) - 4096))
        dc_bad = list(dc)
        spliced = data[:cut] + other[osrc:osrc + 4096]
        r_bad = J.decode_mcus(hdr, spliced, pre.state, max_mcus=60, dc_out=dc_bad)
        survived.append(r_bad.mcus_decoded)
        if r_bad.status == J.COMPLETE:
            fails.append("foreign splice reported COMPLETE")
        if r_bad.mcus_decoded > 0:
            foreign_costs.append(
                J.dc_continuity_cost(dc_bad, hdr.mcus_x, lo, lo + r_bad.mcus_decoded))

    survived.sort()
    print("\nforeign fragment: MCUs decoded before Huffman desync (n=%d)" % len(survived))
    print("  min=%d median=%d max=%d  <- high values confirm Huffman alone is weak"
          % (survived[0], survived[len(survived) // 2], survived[-1]))

    correct_costs.sort()
    foreign_costs.sort()
    c_med = correct_costs[len(correct_costs) // 2]
    f_med = foreign_costs[len(foreign_costs) // 2]
    print("\nDC continuity cost (mean |dDC| vs MCU row above), lower = smoother:")
    print("  correct continuation : median=%7.2f  p90=%7.2f"
          % (c_med, correct_costs[int(len(correct_costs) * 0.9)]))
    print("  foreign continuation : median=%7.2f  p10=%7.2f"
          % (f_med, foreign_costs[int(len(foreign_costs) * 0.1)]))
    print("  separation ratio     : %.1fx" % (f_med / c_med if c_med else float("inf")))

    # overlap = how often a foreign fragment looks smoother than the median
    # correct one. This is the error rate the beam search has to survive.
    overlap = sum(1 for f in foreign_costs if f <= correct_costs[-1])
    print("  foreign costs below WORST correct cost: %d/%d"
          % (overlap, len(foreign_costs)))
    if f_med < c_med * 2:
        fails.append("DC continuity does not separate correct from foreign "
                     "(ratio %.2f) -- beam search will not work" % (f_med / c_med))

    print()
    if fails:
        for f in fails:
            print("FAIL: %s" % f)
        return 1
    print("ALL DECODER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
