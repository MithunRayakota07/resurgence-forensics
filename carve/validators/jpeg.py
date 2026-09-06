"""
Baseline-JPEG entropy-stream decoder used as a HARD CONSTRAINT for carving.

Why this file exists
--------------------
The single most useful question in fragmented-JPEG carving is not
"what file type is this block?" (the entire FFT-75 literature) but:

    "If I continue Huffman-decoding from where fragment A ran out,
     does fragment B let me keep decoding valid MCUs, or does the
     decoder desync within a few dozen bytes?"

Compressed data has almost no exploitable byte-level statistics -- that is
what compression *is*. The exploitable signal lives at the seam, in the
decoder state, so the decoder is the discriminator.

We tested whether a learned model could take over here and it cannot: 0.563
AUC on JPEG entropy-stream adjacency, below a 256-bin byte-histogram baseline,
under both far- and near-negative sampling. For compressed formats there is no
learned component and no claim of one. The learned model belongs on documents
and spreadsheets, where it measures 0.838 / 0.799.

This decoder therefore does NOT do the IDCT, colour conversion, or produce
pixels. It walks the entropy-coded segment symbol by symbol and reports how
far it got. Skipping the IDCT is roughly a 10x speedup and loses nothing:
Huffman desync is what tells us a fragment is foreign.

Scope: baseline sequential DCT (SOF0) only. Progressive (SOF2) is rejected
explicitly rather than silently mis-decoded.

State is snapshottable at MCU boundaries, which is what makes beam search
affordable: extending a candidate path by one 4 KiB cluster re-decodes only
that cluster, not the whole file from the start.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------- markers ---

M_SOF0 = 0xC0
M_SOF2 = 0xC2
M_DHT = 0xC4
M_SOI = 0xD8
M_EOI = 0xD9
M_SOS = 0xDA
M_DQT = 0xDB
M_DRI = 0xDD
M_RST0 = 0xD0
M_RST7 = 0xD7

SOI_BYTES = b"\xff\xd8\xff"
EOI_BYTES = b"\xff\xd9"


# ------------------------------------------------------------- exceptions ---

class NeedMoreData(Exception):
    """Ran out of bytes mid-symbol. Not an error -- ask for another cluster."""


class Desync(Exception):
    """The bitstream stopped being a valid JPEG. This fragment is foreign."""


class MarkerFound(Exception):
    """Hit a real (non-stuffed) marker inside the entropy stream."""

    def __init__(self, marker: int, pos: int):
        super().__init__("marker 0x%02X at %d" % (marker, pos))
        self.marker = marker
        self.pos = pos


class HeaderIncomplete(Exception):
    """The header itself is split across fragments -- cannot start yet."""


# ---------------------------------------------------------- huffman tables ---

class HuffTable:
    """
    Canonical JPEG Huffman table with an 8-bit fast lookup.

    The overwhelming majority of codes in real JPEGs are <= 8 bits, so a
    256-entry direct lookup resolves most symbols in one step. Longer codes
    fall through to the canonical mincode/maxcode walk.
    """

    __slots__ = ("fast", "mincode", "maxcode", "valptr", "symbols")

    def __init__(self, counts, symbols: bytes):
        self.symbols = symbols
        self.mincode = [0] * 17
        self.maxcode = [-1] * 17
        self.valptr = [0] * 17

        code = 0
        k = 0
        for length in range(1, 17):
            n = counts[length - 1]
            self.valptr[length] = k
            self.mincode[length] = code
            self.maxcode[length] = code + n - 1 if n else -1
            code += n
            k += n
            code <<= 1

        # 8-bit fast table: index = next 8 bits -> (symbol, bitlength)
        self.fast = [(-1, 0)] * 256
        code = 0
        k = 0
        for length in range(1, 9):
            n = counts[length - 1]
            for _ in range(n):
                sym = symbols[k]
                lo = code << (8 - length)
                hi = lo + (1 << (8 - length))
                for idx in range(lo, hi):
                    self.fast[idx] = (sym, length)
                code += 1
                k += 1
            code <<= 1


# ------------------------------------------------------------- bit reader ---

class BitReader:
    """
    MSB-first bit reader over a raw JPEG entropy segment.

    Handles JPEG byte stuffing (0xFF 0x00 encodes a literal 0xFF) inline and
    raises MarkerFound on a genuine marker. Bits are pulled in whole bytes
    into an integer accumulator rather than one at a time; that is worth
    roughly 30x over the naive loop, and is the difference between a beam
    search that returns in seconds and one that returns in minutes.
    """

    __slots__ = ("buf", "bp", "bitbuf", "bitcnt", "pad", "marker")

    # The encoder pads the final entropy byte to a byte boundary and then
    # writes EOI. Our reader prefetches up to 16 bits, so decoding the LAST
    # MCU legitimately needs a couple of bytes that do not exist. Feed
    # spec-conformant 1-bits for a strictly bounded budget: enough to finish
    # a trailing symbol, nowhere near enough to rescue a wrong fragment.
    PAD_BUDGET_BITS = 24

    def __init__(self, buf: bytes, bp: int = 0, bitbuf: int = 0, bitcnt: int = 0):
        self.buf = buf
        self.bp = bp
        self.bitbuf = bitbuf
        self.bitcnt = bitcnt
        self.pad = 0
        self.marker = None

    def state(self):
        return (self.bp, self.bitbuf, self.bitcnt)

    def _fill(self, n: int) -> None:
        buf = self.buf
        blen = len(buf)
        while self.bitcnt < n:
            if self.bp >= blen:
                raise NeedMoreData()
            b = buf[self.bp]
            if b == 0xFF:
                if self.bp + 1 >= blen:
                    raise NeedMoreData()
                nxt = buf[self.bp + 1]
                if nxt == 0x00:
                    self.bp += 2          # stuffed literal 0xFF
                else:
                    # A genuine marker. Do NOT advance bp -- callers rely on
                    # bp pointing at the marker so EOI can be detected.
                    self.marker = nxt
                    if self.pad >= self.PAD_BUDGET_BITS:
                        raise MarkerFound(nxt, self.bp)
                    self.pad += 8
                    self.bitbuf = ((self.bitbuf << 8) | 0xFF) & 0xFFFFFFFFFFFFFF
                    self.bitcnt += 8
                    continue
            else:
                self.bp += 1
            self.bitbuf = ((self.bitbuf << 8) | b) & 0xFFFFFFFFFFFFFF
            self.bitcnt += 8

    def peek(self, n: int) -> int:
        self._fill(n)
        return (self.bitbuf >> (self.bitcnt - n)) & ((1 << n) - 1)

    def get(self, n: int) -> int:
        if n == 0:
            return 0
        self._fill(n)
        self.bitcnt -= n
        return (self.bitbuf >> self.bitcnt) & ((1 << n) - 1)

    def align(self) -> None:
        """Discard bits back to a byte boundary (used before restart markers)."""
        self.bitcnt -= self.bitcnt % 8

    def decode_huff(self, tbl: HuffTable) -> int:
        sym, length = tbl.fast[self.peek(8)]
        if length:
            self.bitcnt -= length
            return sym
        length = 8
        while length < 16:
            length += 1
            code = self.peek(length)
            mx = tbl.maxcode[length]
            if mx >= 0 and code <= mx:
                self.bitcnt -= length
                return tbl.symbols[tbl.valptr[length] + code - tbl.mincode[length]]
        raise Desync("no huffman code within 16 bits")

    def receive_extend(self, s: int) -> int:
        if s == 0:
            return 0
        v = self.get(s)
        if v < (1 << (s - 1)):
            v -= (1 << s) - 1
        return v


# ----------------------------------------------------------------- header ---

@dataclass
class Component:
    cid: int
    h: int
    v: int
    tq: int
    dc_tbl: int = 0
    ac_tbl: int = 0


@dataclass
class JpegHeader:
    width: int
    height: int
    components: list
    huff_dc: dict
    huff_ac: dict
    restart_interval: int
    scan_start: int          # byte offset of first entropy byte, relative to buf
    hmax: int
    vmax: int
    mcus_x: int
    mcus_y: int

    @property
    def total_mcus(self) -> int:
        return self.mcus_x * self.mcus_y

    @property
    def blocks_per_mcu(self) -> int:
        return sum(c.h * c.v for c in self.components)

    def describe(self) -> str:
        sub = "x".join("%d:%d" % (c.h, c.v) for c in self.components)
        return "%dx%d, %d comp (%s), %d MCUs, restart=%d" % (
            self.width, self.height, len(self.components), sub,
            self.total_mcus, self.restart_interval,
        )


def parse_header(data: bytes, start: int = 0) -> JpegHeader:
    """
    Parse markers from `data` beginning at `start` (must point at SOI) up to
    and including SOS. Raises HeaderIncomplete if truncated, Desync if this
    is not a baseline JPEG we can handle.
    """
    n = len(data)
    p = start
    if n - p < 2 or data[p] != 0xFF or data[p + 1] != M_SOI:
        raise Desync("no SOI at start")
    p += 2

    huff_dc = {}
    huff_ac = {}
    comps = []
    restart_interval = 0
    width = height = 0

    while True:
        while p < n and data[p] != 0xFF:
            p += 1
        while p < n and data[p] == 0xFF:
            p += 1
        if p >= n:
            raise HeaderIncomplete("ran out before SOS")
        marker = data[p]
        p += 1

        if marker in (M_SOI, 0x01) or M_RST0 <= marker <= M_RST7:
            continue

        if p + 2 > n:
            raise HeaderIncomplete("truncated segment length")
        seg_len = (data[p] << 8) | data[p + 1]
        if seg_len < 2:
            raise Desync("bad segment length")
        if p + seg_len > n:
            raise HeaderIncomplete("truncated segment body")
        seg = data[p + 2 : p + seg_len]
        seg_end = p + seg_len

        if marker == M_SOF2:
            raise Desync("progressive JPEG (SOF2) not supported")

        if marker == M_SOF0:
            if len(seg) < 6:
                raise Desync("short SOF0")
            height = (seg[1] << 8) | seg[2]
            width = (seg[3] << 8) | seg[4]
            ncomp = seg[5]
            comps = []
            o = 6
            for _ in range(ncomp):
                comps.append(Component(seg[o], seg[o + 1] >> 4, seg[o + 1] & 15, seg[o + 2]))
                o += 3

        elif marker == M_DHT:
            o = 0
            while o + 17 <= len(seg):
                tc = seg[o] >> 4
                th = seg[o] & 15
                counts = list(seg[o + 1 : o + 17])
                total = sum(counts)
                symbols = bytes(seg[o + 17 : o + 17 + total])
                if len(symbols) != total:
                    raise Desync("truncated DHT")
                tbl = HuffTable(counts, symbols)
                if tc == 0:
                    huff_dc[th] = tbl
                else:
                    huff_ac[th] = tbl
                o += 17 + total

        elif marker == M_DRI:
            restart_interval = (seg[0] << 8) | seg[1]

        elif marker == M_SOS:
            ns = seg[0]
            o = 1
            by_id = {c.cid: c for c in comps}
            scan_comps = []
            for _ in range(ns):
                cid = seg[o]
                c = by_id.get(cid)
                if c is None:
                    raise Desync("SOS references unknown component %d" % cid)
                c.dc_tbl = seg[o + 1] >> 4
                c.ac_tbl = seg[o + 1] & 15
                scan_comps.append(c)
                o += 2
            if len(scan_comps) != len(comps):
                raise Desync("non-interleaved scan not supported")
            if not comps or not width or not height:
                raise Desync("SOS before SOF0")
            hmax = max(c.h for c in comps)
            vmax = max(c.v for c in comps)
            return JpegHeader(
                width=width,
                height=height,
                components=comps,
                huff_dc=huff_dc,
                huff_ac=huff_ac,
                restart_interval=restart_interval,
                scan_start=seg_end,
                hmax=hmax,
                vmax=vmax,
                mcus_x=math.ceil(width / (8 * hmax)),
                mcus_y=math.ceil(height / (8 * vmax)),
            )

        p = seg_end


# ------------------------------------------------------------ decode state ---

NEED_MORE = "NEED_MORE"   # ran out of data at an MCU boundary -> extend the path
DESYNC = "DESYNC"         # bitstream invalid -> this candidate is foreign
COMPLETE = "COMPLETE"     # decoded exactly total_mcus AND the scan ends at EOI
EARLY_EOI = "EARLY_EOI"   # EOI before all MCUs -> truncated / wrong
NO_EOI = "NO_EOI"         # right MCU count, but the scan does not end here


@dataclass(frozen=True)
class DecodeState:
    """Resumable decoder position. Only ever captured at an MCU boundary."""

    bp: int
    bitbuf: int
    bitcnt: int
    dc_preds: tuple
    mcu_index: int
    since_restart: int = 0

    @staticmethod
    def initial(hdr: JpegHeader, bp: int) -> "DecodeState":
        return DecodeState(bp, 0, 0, tuple(0 for _ in hdr.components), 0, 0)


@dataclass
class DecodeResult:
    status: str
    state: DecodeState        # rolled back to the last clean MCU boundary
    mcus_decoded: int         # MCUs decoded during THIS call
    bytes_consumed: int       # entropy bytes consumed during THIS call
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (NEED_MORE, COMPLETE)


def dc_continuity_cost(dc: list, mcus_x: int, lo: int, hi: int) -> float:
    """
    Mean absolute luma-DC difference against the MCU directly ABOVE, over
    MCUs [lo, hi).

    This is the real discriminator for fragmented JPEG, and it took a failing
    test to make that obvious. Two JPEGs produced by the same encoder share
    the standard Huffman tables, so a foreign fragment is still *valid*
    Huffman data -- it decodes happily for thousands of symbols. What it
    cannot do is stay photometrically consistent: the per-MCU luma DC term is
    average block brightness, so in a real image it varies smoothly against
    its vertical neighbour, while a foreign fragment random-walks.

    Vertical rather than horizontal because DC is differentially coded along
    the scan line, so horizontal smoothness is partly baked in by the codec;
    the row above is an independent check.
    """
    lo = max(lo, mcus_x)
    hi = min(hi, len(dc))
    if hi <= lo:
        return 0.0
    total = 0.0
    n = 0
    for i in range(lo, hi):
        above = dc[i - mcus_x]
        if above is None or dc[i] is None:
            continue
        total += abs(dc[i] - above)
        n += 1
    return total / n if n else 0.0


def plausible_scan_data(block: bytes) -> bool:
    """
    Cheap precondition: could this block be part of a JPEG entropy-coded scan?

    A JPEG scan segment is compressed binary. It is NOT plain text. Measured on
    the NIST CFReDS corpus, the inter-fragment fill is literally the ASCII
    string "*** FILL TEXT BLOCK ***" repeated -- 100% printable, entropy 3.20,
    and not one 0xFF byte in 855 sectors, against 7.54 entropy for real scan
    data.

    Our decoder accepted it anyway. Huffman-decoding repeating ASCII yields a
    periodic symbol stream and therefore smooth-looking DC values, which is why
    the photometric check could not see it either: the fill scored 0.890
    against 0.917 for genuine image rows. The search walked straight through it
    for three separate attempted fixes.

    An entropy floor alone is the wrong instrument -- legitimate header blocks
    carrying EXIF measure as low as 1.95, and zero-padded tail blocks 0.54. The
    discriminating feature is that the fill is ENTIRELY text. The one exception
    is a file's final block, which is part scan data and part whatever follows
    it, so it can read as almost pure text; that block is identified instead by
    carrying the EOI marker.

    General, not a benchmark special case: real-world inter-fragment data that
    happens to be text is correctly rejected, and data that happens to be
    another JPEG is correctly NOT rejected -- that case still needs the harder
    discrimination, and this test makes no claim about it.
    """
    if not block:
        return False
    printable = 0
    for c in block:
        if 32 <= c < 127 or c in (9, 10, 13):
            printable += 1
    if printable * 100 < len(block) * 99:
        return True                       # has real binary content
    return EOI_BYTES in block             # pure text, but may carry the file end


def dc_seam_correlation(dc: list, mcus_x: int, seam: int, base: int = 0):
    """
    Structural agreement across the seam: Pearson correlation between the
    candidate's first MCU row and the MCU row directly above it, which lies
    in the previous fragment.

    Returns (cost, corr, n) with cost = 1 - corr in [0, 2].

    Absolute |dDC| looked like the obvious metric and is actively harmful.
    Because DC is differentially coded, a foreign fragment INHERITS the
    predecessor's predictor as a constant offset -- so its DC values start
    right next to the correct ones, and a flat, low-detail fragment scores a
    near-zero difference no matter where it came from. Beam search found that
    exploit immediately and filled up with flat garbage.

    Correlation is offset-free and scale-free, so it asks the question we
    actually mean: does the image structure CONTINUE here? A fragment with no
    vertical variance has no evidence of continuation at all, and is scored
    as such (cost 1.0) rather than rewarded for being featureless.
    """
    if seam < mcus_x or seam - mcus_x < base:
        return 0.0, 1.0, 0
    lo_above = seam - mcus_x - base
    lo_new = seam - base
    k = min(mcus_x, len(dc) - lo_new)
    if k < 8:
        return 1.0, 0.0, 0
    a, b = [], []
    for j in range(k):
        u, v = dc[lo_above + j], dc[lo_new + j]
        if u is None or v is None:
            continue
        a.append(u)
        b.append(v)
    n = len(a)
    if n < 8:
        return 1.0, 0.0, n
    ma = sum(a) / n
    mb = sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 1e-9 or vb <= 1e-9:
        return 1.0, 0.0, n          # featureless: no evidence, not free credit
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    corr = cov / (va ** 0.5 * vb ** 0.5)
    return 1.0 - corr, corr, n


def dc_seam_cost(dc: list, mcus_x: int, seam: int, base: int = 0) -> float:
    """
    DC discontinuity measured ONLY where the candidate touches the previous
    fragment: the first `mcus_x` MCUs after the seam, whose vertical
    neighbours are the last MCU row of the old fragment.

    Averaging over the whole candidate (dc_continuity_cost) turned out to be
    much weaker, and the reason is structural. DC is DIFFERENTIALLY coded, so
    a wrong candidate inherits the predecessor's predictor as a constant
    offset -- and that offset CANCELS in any comparison between two MCUs that
    are both inside the candidate. So most of a wide average is really asking
    "is this candidate internally smooth?", which is true of every real
    photograph and discriminates nothing. Only the rows straddling the seam
    carry the joint evidence.

    Fixed-width by construction, so candidates that decode different numbers
    of MCUs stay directly comparable.
    """
    if seam < mcus_x or seam - mcus_x < base:
        return 0.0
    hi = min(len(dc) + base, seam + mcus_x)
    total = 0.0
    n = 0
    for i in range(seam, hi):
        a, b = dc[i - base], dc[i - mcus_x - base]
        if a is None or b is None:
            continue
        total += abs(a - b)
        n += 1
    if n == 0:
        return 0.0
    return total / n


def decode_mcus(
    hdr: JpegHeader,
    buf: bytes,
    state: DecodeState,
    max_mcus: Optional[int] = None,
    dc_out: Optional[list] = None,
    dc_base: int = 0,
) -> DecodeResult:
    """
    Resume decoding `buf` from `state` until we run out of data, desync, or
    finish the image.

    `buf` must be the concatenation of every fragment in the candidate path,
    so byte offsets held in `state` stay meaningful as the path grows.

    If `dc_out` is given it must satisfy
    `dc_base + len(dc_out) == state.mcu_index`; the mean luma DC of each
    decoded MCU is appended. `dc_base` is the MCU index of dc_out[0], which
    lets callers keep only a trailing window instead of one float per MCU of
    the whole file -- at 47,628 MCUs a per-path full trace was the dominant
    term in a 1 GB memory blowup. It is truncated back to the last clean MCU
    boundary on exit, so a rejected candidate never leaves residue behind.
    """
    r = BitReader(buf, state.bp, state.bitbuf, state.bitcnt)
    dc = list(state.dc_preds)
    mcu = state.mcu_index
    since_restart = state.since_restart
    total = hdr.total_mcus
    comps = hdr.components
    ri = hdr.restart_interval

    start_mcu = mcu
    start_bp = state.bp
    safe = state          # last known-good MCU boundary

    try:
        while mcu < total:
            if max_mcus is not None and (mcu - start_mcu) >= max_mcus:
                break

            if ri and since_restart == ri:
                # restart marker: byte-align, expect FFD0..FFD7, reset predictors
                r.align()
                r.bitcnt = 0
                r.bitbuf = 0
                if r.bp + 1 >= len(buf):
                    raise NeedMoreData()
                if buf[r.bp] != 0xFF or not (M_RST0 <= buf[r.bp + 1] <= M_RST7):
                    raise Desync("expected restart marker")
                r.bp += 2
                dc = [0] * len(comps)
                since_restart = 0

            luma_sum = 0
            luma_n = 0
            for ci, c in enumerate(comps):
                dtbl = hdr.huff_dc.get(c.dc_tbl)
                atbl = hdr.huff_ac.get(c.ac_tbl)
                if dtbl is None or atbl is None:
                    raise Desync("scan references a Huffman table we never saw")
                for _ in range(c.h * c.v):
                    t = r.decode_huff(dtbl)
                    if t > 15:
                        raise Desync("DC category %d out of range" % t)
                    dc[ci] += r.receive_extend(t)
                    if ci == 0:
                        luma_sum += dc[0]
                        luma_n += 1
                    k = 1
                    while k < 64:
                        rs = r.decode_huff(atbl)
                        s = rs & 15
                        run = rs >> 4
                        if s == 0:
                            if run == 15:
                                # ZRL: skip 16 coefficients. This path used to
                                # add 16 with NO bounds check, so a block that
                                # was already near full silently ran past index
                                # 63 and the `while k < 64` test just exited as
                                # if the block had ended cleanly -- swallowing a
                                # quantization-array overflow instead of
                                # flagging it. This is exactly the QA-overflow
                                # validation of van der Meer, van den Bos,
                                # Jonker & Dassen, "Problem solved: A reliable,
                                # deterministic method for JPEG fragmentation
                                # point detection", DFRWS EU 2024: a foreign
                                # fragment decodes for a while, then a ZRL that
                                # does not fit the 64-coefficient array proves
                                # the bytes are not a valid continuation.
                                # k==48 exactly fills indices 48..63; k>48 after
                                # would place a coefficient beyond 63.
                                if k + 16 > 64:
                                    raise Desync("QA overflow: ZRL past coefficient 63")
                                k += 16
                                continue
                            break                      # EOB
                        k += run
                        if k > 63:
                            raise Desync("QA overflow: AC run past coefficient 63")
                        r.receive_extend(s)
                        k += 1

            mcu += 1
            since_restart += 1
            if dc_out is not None:
                dc_out.append(luma_sum / luma_n if luma_n else None)
            bp, bitbuf, bitcnt = r.state()
            safe = DecodeState(bp, bitbuf, bitcnt, tuple(dc), mcu, since_restart)

    except NeedMoreData:
        return _finish(NEED_MORE, safe, start_mcu, start_bp, dc_out, "out of data", dc_base)
    except Desync as e:
        return _finish(DESYNC, safe, start_mcu, start_bp, dc_out, str(e), dc_base)
    except MarkerFound as e:
        if e.marker == M_EOI:
            if safe.mcu_index >= total:
                return _finish(COMPLETE, safe, start_mcu, start_bp, dc_out,
                               "EOI at full MCU count", dc_base)
            return _finish(EARLY_EOI, safe, start_mcu, start_bp, dc_out,
                           "EOI after %d/%d MCUs" % (safe.mcu_index, total), dc_base)
        return _finish(DESYNC, safe, start_mcu, start_bp, dc_out,
                       "marker 0x%02X mid-scan" % e.marker, dc_base)

    if mcu >= total:
        # Hard global constraint: decoding the right NUMBER of MCUs is not
        # enough. Huffman self-synchronises, so a wrong cluster chain will
        # happily decode 1900 valid-looking MCUs and stop on the counter.
        # The scan must actually END here -- padded to a byte boundary and
        # followed immediately by EOI. Dropping this check let the carver
        # report byte-perfect confidence on files it had assembled wrongly.
        window = buf[max(0, safe.bp - 2) : safe.bp + 4]
        if EOI_BYTES in window:
            return _finish(COMPLETE, safe, start_mcu, start_bp, dc_out,
                           "all MCUs decoded, EOI present", dc_base)
        return _finish(NO_EOI, safe, start_mcu, start_bp, dc_out,
                       "all %d MCUs decoded but no EOI at the scan end" % total, dc_base)

    return _finish(NEED_MORE, safe, start_mcu, start_bp, dc_out, "max_mcus reached", dc_base)


def _finish(status, safe, start_mcu, start_bp, dc_out, detail, dc_base=0) -> DecodeResult:
    """Roll the DC trace back to the last clean MCU boundary and package up."""
    if dc_out is not None and dc_base + len(dc_out) > safe.mcu_index:
        del dc_out[safe.mcu_index - dc_base :]
    return DecodeResult(status, safe, safe.mcu_index - start_mcu,
                        safe.bp - start_bp, detail)
