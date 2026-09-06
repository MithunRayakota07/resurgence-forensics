"""
Reassembly as constrained beam search over cluster sequences.

The graph formulation is not new -- Shanmugasundaram & Memon (2003) and Pal &
Memon (2006) framed reassembly as a maximum-weight path / k-vertex-disjoint
path problem two decades ago, and solved it with greedy heuristics (PUP).
Two things here are different:

  1. We keep N partial paths alive instead of committing greedily, so one
     bad local choice does not doom the file.
  2. Format structure is a HARD CONSTRAINT that prunes the graph, not a
     weight that gets averaged away. A candidate that desyncs the Huffman
     decoder, or that ends the image before every MCU is accounted for, is
     eliminated -- not down-weighted.

Scoring, per extension a -> b:

    hard    : decoder status must be NEED_MORE or COMPLETE
    soft    : DC continuity cost of the newly decoded MCUs (photometric)
    soft    : log P(gap | filesystem allocation policy)   [ablatable]

For LOW-ENTROPY formats (documents, spreadsheets) the Phase 1 learned model
becomes a fourth term and takes over as the primary signal, measured at 0.838
AUC on XLS and 0.799 on DOC against near-negatives.

For COMPRESSED formats it does not, and we make no such claim: learned
adjacency measures 0.563 on JPEG entropy-stream data, below a byte-histogram
baseline. Here the decoder and the global constraints are the contribution.
That split was settled by measurement, not preference.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from carve.validators import jpeg as J

# Scoring weights.
#
# W_PRIOR started at 6.0 and that was wrong in an instructive way: the
# contiguous bonus handed the next-cluster-along a ~35 point advantage, which
# is larger than any photometric evidence the seam can produce, so the search
# followed filler straight past a fragment boundary. The allocation prior is
# a TIE-BREAKER among candidates the evidence already likes -- the moment it
# can overrule the evidence it is just encoding the answer we wanted.
W_CORR = 40.0       # structural agreement across the seam (primary evidence)
W_SEAM = 0.15       # residual |dDC| at the seam (secondary)
W_PRIOR = 1.5       # allocation prior (tie-breaker only)
ENTROPY_FLOOR = 6.5  # cheap pre-filter for candidate clusters

# Beam paths this far below the leader are dropped even if a slot is free.
# After a branch the beam fills with near-duplicates of a wrong prefix, and
# carrying them costs a full candidate sweep each per step for no benefit --
# they lose on the very next seam. Width alone does not bound that; margin
# does. Set wide enough that the true path, which can sit a few ranks down at
# a genuine fragment boundary, is never the thing being discarded.
SCORE_MARGIN = 90.0


@dataclass
class Path:
    """
    A candidate cluster ordering, holding only a SLIDING WINDOW of decoder
    state rather than the whole file so far.

    `buf` used to be every byte accepted to date and `dc` one float per MCU of
    the whole image. With 24 probe children per beam slot that reached 1 GB on
    a single 2.7 MB photograph. The decoder never reads backwards, and the
    seam correlation never looks further back than one MCU row, so both can be
    trimmed to a trailing window: `buf` starts at byte `buf_base` of the
    concatenated clusters, and `dc[0]` is MCU number `dc_base`.
    """

    clusters: list
    buf: bytes
    state: J.DecodeState
    dc: list
    buf_base: int = 0
    dc_base: int = 0
    score: float = 0.0
    status: str = J.NEED_MORE
    steps: list = field(default_factory=list)   # per-step diagnostics
    dc_hist: list = field(default_factory=list)  # accepted seam CORRELATIONS
    verify_seam: int = 0        # MCU index where the current unverified run began
    checkpoint: object = None   # last VERIFIED path, to rewind to
    force_branch: bool = False  # set after a rewind: this path may not run-merge

    @property
    def mcus(self) -> int:
        return self.state.mcu_index

    def key(self) -> tuple:
        return tuple(self.clusters)

    def corr_threshold(self) -> float:
        """
        Self-calibrating bound on seam correlation for continuing a RUN
        without branching.

        Measured on easy.img: within a fragment, consecutive clusters
        correlate 0.69-0.92; at a genuine fragment boundary the next cluster
        along drops well below 0.56. So a run continues while the evidence
        stays near this file's own established level, and the search only
        opens up when it does not.

        An absolute constant will not do -- what counts as smooth depends on
        the image. An early version used a fixed |dDC| < 60, which sat inside
        the overlap between correct and foreign continuations, and the carver
        silently swallowed filler clusters.

        This reads accepted seam CORRELATIONS. It used to read the list while
        it held costs (1 - corr) and compare the result against a correlation,
        which is a unit mismatch: it only ever behaved because the max() floor
        swallowed the wrong number nearly every time.

        """
        if len(self.dc_hist) < 3:
            return 0.55
        s = sorted(self.dc_hist)
        med = s[len(s) // 2]
        return max(0.35, 0.70 * med)


@dataclass
class CarveResult:
    ok: bool
    clusters: list
    data: bytes
    mcus: int
    total_mcus: int
    score: float
    margin: float             # best vs second-best final score
    n_fragments: int
    elapsed_s: float
    candidates_scored: int
    decoder_calls: int
    trace: list
    reason: str = ""


def _fragments(clusters) -> int:
    """Count contiguous runs -- the fragmentation level of the recovered file."""
    if not clusters:
        return 0
    n = 1
    for a, b in zip(clusters, clusters[1:]):
        if b != a + 1:
            n += 1
    return n


class BeamCarver:
    # Search scale is expressed in BYTES of disk, not in cluster counts.
    # Cluster size is a formatting choice, so a window of "160 clusters" means
    # 640 KB on a 4 KiB volume and only 80 KB on a 512 B one -- the search
    # silently shrank 8x when we moved to the NIST corpus, and every true
    # successor sat outside it. Inter-fragment gaps there run 14 to 301
    # sectors; a cluster-counted window could not reach them.
    WINDOW_BYTES = 1 << 20          # how far away a successor may plausibly sit
    MAX_CANDIDATES = 2048           # branches are rare, so afford a wide sweep
    PROBE_TOP_K = 24                # shortlist re-scored over a full MCU row
    # Depth at which a candidate's FEASIBILITY is judged, in bytes past the
    # seam. van der Meer, van den Bos, Jonker & Dassen (DFRWS EU 2024) measure
    # their bit-level validator as invalidating a wrong continuation with
    # >99.4% probability within 4096 bytes -- and 4 KB is also the NTFS/exFAT
    # cluster size their result is stated for. We were deciding on a SINGLE
    # 512 B sector, i.e. 8x shallower than the window that guarantee applies
    # to, which is why foreign candidates kept surviving into the beam: at one
    # sector the true successor gains as few as 6 MCUs while foreign blocks
    # gain 13-15, so the admission decision was pure noise.
    VALIDATE_BYTES = 4096
    # A candidate that desyncs INSIDE the window has not necessarily failed --
    # it may simply be a short fragment that legitimately ended. Our synthetic
    # corpus has 5-cluster fragments, so requiring the full window would reject
    # correct answers and break results that are byte-exact today. Foreign data
    # dies far sooner than that (measured: <1 cluster, ~15 MCUs, at jump.jpg's
    # true fragmentation point), so this bound separates the two cleanly
    # without assuming a minimum fragment length we cannot know.
    VALIDATE_MIN_SECTORS = 4
    PROBE_MAX_BLOCKS = 96           # cap on blocks read while probing one candidate

    def __init__(self, view, prior, beam_width: int = 24, window: int = None,
                 max_candidates: int = None, max_steps: int = None,
                 collect_trace: bool = True, on_step=None, deep_validation: bool = False):
        self.view = view
        self.prior = prior
        self.beam_width = beam_width
        self.window = (window if window is not None
                       else max(8, self.WINDOW_BYTES // view.cluster_size))
        self.max_candidates = (max_candidates if max_candidates is not None
                               else self.MAX_CANDIDATES)
        self.probe_top_k = self.PROBE_TOP_K
        self.probe_max_blocks = self.PROBE_MAX_BLOCKS
        self.validate_sectors = max(1, self.VALIDATE_BYTES // view.cluster_size)
        self.validate_min_sectors = min(self.VALIDATE_MIN_SECTORS,
                                        self.validate_sectors)
        # OFF by default -- measured, and it does not pay for itself.
        #
        # Validating each candidate to the full DFRWS-2024 4 KB window rejects
        # only 71 of 1661 candidates (4%) at jump.jpg's true fragmentation
        # point; 1590 survive the whole window and the true successor still
        # ranks 87th. CFReDS stays 0/6. hard.img stays byte-exact but goes from
        # 20.3 s to 85.1 s -- 4.2x the runtime to prune 4%.
        #
        # The reason it cannot pay is section 5.3 of that paper: their >99.4%-
        # within-4-KB guarantee is measured against RANDOM data injected after
        # the fragmentation point. Our competitors are other JPEGs from the
        # same disk, which share the standard Huffman tables and therefore
        # decode cleanly. Measured with the same validator and window:
        # random 300/300 = 100% rejected, real JPEG 373/720 = 51.8%.
        # See CLAUDE.md 5f. Keep the code -- it is how that result was
        # produced, and it is the right gate if a corpus ever supplies random
        # or non-JPEG filler -- but do not pay 4x for it by default.
        self.deep_validation = deep_validation
        # A path can never need more steps than there are clusters. The old
        # fixed 4096 was sized for 16 MiB images at 4 KiB clusters; on the NIST
        # corpus at 512 B, dino.jpg's FIRST fragment alone is 6020 sectors, so
        # the search ran out of steps before it ever reached a fragment
        # boundary and reported a confident-looking partial failure.
        self.max_steps = max_steps if max_steps is not None else len(view) + 8
        self.collect_trace = collect_trace
        # Emit each search step as it happens rather than only at the end.
        # Buffering the whole trace left the UI blank for the entire search,
        # which reads as a hang -- the exploration IS the thing worth showing.
        self.on_step = on_step
        self.entropies = view.entropies()
        self._high = None

    # ------------------------------------------------------------ helpers --
    def high_entropy_pool(self):
        if self._high is None:
            self._high = [int(c) for c in range(len(self.view))
                          if self.entropies[c] >= ENTROPY_FLOOR]
        return self._high

    def candidates_for(self, path: Path) -> list:
        cur = path.clusters[-1]
        used = set(path.clusters)
        n = len(self.view)

        if self.prior.allows_greedy_run():
            # locality-aware: a window around the current position, plus the
            # immediate successors unconditionally (the tail cluster of a file
            # is zero-padded and can fall below the entropy floor).
            lo = max(0, cur - self.window)
            hi = min(n, cur + self.window + 1)
            pool = {c for c in range(lo, hi)
                    if c not in used and self.entropies[c] >= ENTROPY_FLOOR}
            for d in (1, 2):
                if cur + d < n and cur + d not in used:
                    pool.add(cur + d)
        else:
            # uniform ablation: no locality may leak in, so every plausible
            # cluster on the whole image is a candidate. Slower on purpose --
            # that cost IS part of what the prior buys.
            pool = {c for c in self.high_entropy_pool() if c not in used}

        ranked = self.prior.rank_candidates(cur, pool)
        return ranked[: self.max_candidates]

    def probe(self, hdr, parent: Path, child: Path, cand: int, log_prior: float):
        """
        Re-score a shortlisted candidate over a FULL MCU row.

        Extends `child` sequentially (a fragment is contiguous by definition,
        so its own successor is the next block along) until a whole MCU row of
        evidence has accumulated past the seam, then recomputes the seam
        correlation across that row and replaces the child's score with it.

        Returns (probed_path, decoder_calls_used).
        """
        seam = parent.state.mcu_index
        target = seam + hdr.mcus_x
        p = child
        calls = 0
        while p.mcus < target and calls < self.probe_max_blocks:
            nxt = p.clusters[-1] + 1
            if nxt >= len(self.view) or nxt in set(p.clusters):
                break
            cand_path, _diag = self.extend(hdr, p, nxt, self.prior.log_gap_prob(1))
            calls += 1
            if cand_path is None:
                break                      # fragment ended; judge on what we have
            p = cand_path

        if calls == 0:
            # One block already covered a full MCU row, so the probe learned
            # nothing new. Leave the score exactly as extend() computed it --
            # rescoring here with a different set of terms silently changed
            # rankings and broke a case that had been byte-exact.
            return p, 0

        corr_cost, corr, n_pairs = J.dc_seam_correlation(p.dc, hdr.mcus_x, seam,
                                                         p.dc_base)
        if n_pairs >= 8:
            # Replace the thin single-block score with the full-row verdict,
            # using the SAME terms as extend() so the two are comparable.
            seam_cost = J.dc_seam_cost(p.dc, hdr.mcus_x, seam, p.dc_base)
            p.score = (parent.score - W_CORR * corr_cost
                       - W_SEAM * seam_cost + W_PRIOR * log_prior)
            p.dc_hist = parent.dc_hist + [corr]
        return p, calls

    def deep_validate(self, hdr, child: Path):
        """
        Hard feasibility gate at the DFRWS-2024 validation depth.

        Extends `child` sequentially -- a fragment is contiguous by definition,
        so its own successor is the next cluster along -- and asks whether the
        bytes keep decoding for 4 KB past the seam. Wrong continuations fail
        this almost immediately; correct ones run to the end of their fragment.

        Returns (feasible, sectors_survived, why). This decides ADMISSION only.
        It never adjusts a score: a candidate either survives the window or it
        is not a valid continuation, which is the whole point of treating the
        validator as a hard constraint rather than another soft term.
        """
        view = self.view
        seen = set(child.clusters)
        state = child.state
        buf = child.buf
        cur = child.clusters[-1]
        survived = 0
        for _ in range(self.validate_sectors):
            nxt = cur + 1
            if nxt >= len(view) or nxt in seen:
                return True, survived, "ran out of sequential data"
            blk = view.cluster(nxt)
            if not J.plausible_scan_data(blk):
                # Fill, not image data: the fragment ended here. That is a
                # legitimate stop, judged by how far the candidate already got.
                return (survived >= self.validate_min_sectors, survived,
                        "hit non-scan data")
            buf = buf + blk
            res = J.decode_mcus(hdr, buf, state)
            if res.status == J.COMPLETE:
                return True, survived, "completed the file"
            if res.status in (J.DESYNC, J.EARLY_EOI, J.NO_EOI):
                return (survived >= self.validate_min_sectors, survived,
                        "stopped: " + res.status)
            state = res.state
            cur = nxt
            seen.add(nxt)
            survived += 1
        return True, survived, "survived the full window"

    def extend(self, hdr, path: Path, cand: int, log_prior: float):
        """Try appending `cand`. Returns (Path|None, diagnostic dict)."""
        # Cheapest hard constraint first, and it must apply to EVERY candidate
        # including sequential ones. The run-merge path used to bypass the
        # candidate filters entirely, which is precisely how the search walked
        # into 855 sectors of plain-text fill on the NIST corpus.
        blk = self.view.cluster(cand)
        if not J.plausible_scan_data(blk):
            return None, {"cand": cand, "gap": cand - path.clusters[-1],
                          "status": "NOT_SCAN", "mcus_gained": 0,
                          "log_prior": round(log_prior, 3), "kept": False,
                          "dc_cost": None, "delta": None,
                          "reason": "not entropy-coded data (plain text)"}
        buf = path.buf + blk
        dc = list(path.dc)
        res = J.decode_mcus(hdr, buf, path.state, dc_out=dc, dc_base=path.dc_base)

        diag = {
            "cand": cand,
            "gap": cand - path.clusters[-1],
            "status": res.status,
            "mcus_gained": res.mcus_decoded,
            "log_prior": round(log_prior, 3),
            "kept": False,
            "dc_cost": None,
            "delta": None,
            "reason": "",
        }

        # ---- hard constraints -------------------------------------------
        if res.status == J.DESYNC:
            diag["reason"] = "desync: " + res.detail
            return None, diag
        if res.status == J.EARLY_EOI:
            diag["reason"] = "EOI before all MCUs"
            return None, diag
        if res.status == J.NO_EOI:
            diag["reason"] = "MCU count reached without EOI"
            return None, diag
        if res.mcus_decoded == 0 and res.status != J.COMPLETE:
            diag["reason"] = "no progress"
            return None, diag

        # ---- soft score --------------------------------------------------
        seam = path.state.mcu_index
        corr_cost, corr, n_pairs = J.dc_seam_correlation(dc, hdr.mcus_x, seam,
                                                         path.dc_base)
        seam_cost = J.dc_seam_cost(dc, hdr.mcus_x, seam, path.dc_base)
        if res.status == J.COMPLETE and n_pairs < 8:
            # The final cluster of a file often carries only a handful of
            # MCUs -- too few for a meaningful correlation. Penalising that
            # would punish the one candidate that just satisfied the
            # strongest constraint we have (every MCU accounted for, scan
            # terminating exactly at EOI). Let the hard constraint speak.
            corr_cost, corr = 0.0, 1.0
        delta = -W_CORR * corr_cost - W_SEAM * seam_cost + W_PRIOR * log_prior
        diag["corr"] = round(corr, 3)
        diag["corr_n"] = n_pairs
        diag["seam_cost"] = round(seam_cost, 2)
        diag["corr_cost"] = round(corr_cost, 3)
        diag["dc_cost"] = round(corr_cost, 3)     # headline number for traces
        diag["delta"] = round(delta, 2)
        diag["kept"] = True

        # ---- slide the window forward -------------------------------------
        # Everything before the decoder's current byte is unreachable, and the
        # seam correlation never looks back further than two MCU rows. Drop
        # the rest so a path costs kilobytes instead of megabytes.
        cs = self.view.cluster_size
        state = res.state
        buf_base = path.buf_base
        keep_bytes = max(cs * 2, 8192)
        if state.bp > keep_bytes:
            trim = ((state.bp - keep_bytes) // cs) * cs
            if trim > 0:
                buf = buf[trim:]
                state = replace(state, bp=state.bp - trim)
                buf_base += trim

        dc_base = path.dc_base
        # Room for the verification window: correlation at `verify_seam`
        # reaches back one MCU row before it, and verification fires a row
        # after it, so three rows of headroom.
        keep_mcus = 3 * hdr.mcus_x + 16
        if len(dc) > keep_mcus:
            drop = len(dc) - keep_mcus
            dc = dc[drop:]
            dc_base += drop

        nxt = Path(
            clusters=path.clusters + [cand],
            buf=buf,
            state=state,
            dc=dc,
            buf_base=buf_base,
            dc_base=dc_base,
            score=path.score + delta,
            status=res.status,
            steps=path.steps + [diag],
            dc_hist=path.dc_hist + [corr],
            verify_seam=path.verify_seam,
            checkpoint=path.checkpoint,
        )
        return nxt, diag

    # --------------------------------------------------------------- run ---
    def carve(self, header_cluster: int) -> CarveResult:
        t0 = time.time()
        view = self.view
        trace = []
        n_scored = 0
        n_decode = 0

        # ---- parse the header ------------------------------------------
        buf = view.cluster(header_cluster)
        try:
            hdr = J.parse_header(buf, 0)
        except J.HeaderIncomplete:
            # The header spans clusters -- pull in sequential successors until
            # it parses. The old limit was three extra clusters, which was
            # enough only because our synthetic photos have ~600 byte headers.
            # Real camera JPEGs carry large EXIF blocks: in the NIST corpus the
            # headers run from 8 to 67 sectors, and grizzly.jpg alone needs
            # 34 KB before the scan starts. Budget by BYTES, not cluster count.
            max_header_bytes = 256 * 1024
            max_extra = max(3, max_header_bytes // view.cluster_size)
            for extra in range(1, max_extra + 1):
                if header_cluster + extra >= len(view):
                    break
                buf += view.cluster(header_cluster + extra)
                try:
                    hdr = J.parse_header(buf, 0)
                    break
                except J.HeaderIncomplete:
                    continue
                except J.Desync as e:
                    return CarveResult(False, [], b"", 0, 0, 0.0, 0.0, 0,
                                       time.time() - t0, 0, 0, trace, str(e))
            else:
                return CarveResult(False, [], b"", 0, 0, 0.0, 0.0, 0,
                                   time.time() - t0, 0, 0, trace,
                                   "header incomplete after %d KB" % (max_header_bytes // 1024))
        except J.Desync as e:
            return CarveResult(False, [], b"", 0, 0, 0.0, 0.0, 0,
                               time.time() - t0, 0, 0, trace, str(e))

        n_header_clusters = max(1, (hdr.scan_start + view.cluster_size - 1)
                                // view.cluster_size)
        start_clusters = list(range(header_cluster,
                                    header_cluster + n_header_clusters))
        buf = view.concat(start_clusters)

        dc = []
        st0 = J.DecodeState.initial(hdr, hdr.scan_start)
        res0 = J.decode_mcus(hdr, buf, st0, dc_out=dc)
        n_decode += 1
        if res0.status == J.DESYNC:
            return CarveResult(False, start_clusters, b"", 0, hdr.total_mcus,
                               0.0, 0.0, 0, time.time() - t0, 0, n_decode, trace,
                               "header cluster desyncs immediately")

        beam = [Path(clusters=start_clusters, buf=buf, state=res0.state, dc=dc,
                     score=0.0, status=res0.status)]
        finished = []

        # ---- search -----------------------------------------------------
        for step in range(self.max_steps):
            if not beam:
                break

            # a path that decoded every MCU is done
            still = []
            for p in beam:
                if p.status == J.COMPLETE and p.mcus >= hdr.total_mcus:
                    finished.append(p)
                else:
                    still.append(p)
            beam = still
            if finished or not beam:
                break

            # ---- expand: run-merge where possible, branch where needed ---
            #
            # Branching is decided PER PATH, not globally. Inside a fragment
            # the successor is simply the next cluster along and the seam
            # correlation says so loudly, so the path extends without
            # branching -- collapsing what would be a 200k-node graph into a
            # few hundred nodes. Only where that evidence fails, i.e. at a
            # genuine fragment boundary, does a path open up into the full
            # candidate set.
            #
            # This shortcut IS the allocation prior in its strongest form, so
            # the uniform ablation refuses it and pays the full search cost.
            expansions = []
            children = []
            n_runs = 0
            for p in beam:
                cur = p.clusters[-1]
                if self.prior.allows_greedy_run() and cur + 1 < len(view) \
                        and (cur + 1) not in set(p.clusters):
                    cand_path, diag = self.extend(hdr, p, cur + 1,
                                                  self.prior.log_gap_prob(1))
                    n_decode += 1
                    n_scored += 1
                    # A low correlation may only VETO a run-merge when it is
                    # computed on enough samples to mean something. One
                    # candidate block must supply a decent share of an MCU row
                    # for the comparison against the row above to be stable.
                    # At 4 KiB on small images it does (90+ samples vs a
                    # 50-wide row). At 512 B on 12-megapixel photos it does
                    # not -- 17 samples against a 162-wide row -- and the noise
                    # vetoed 13% of CORRECT sequential steps, branching the
                    # beam until the search became unaffordable. Where the
                    # evidence is too thin to judge, defer to the decoder,
                    # which desyncs on fill data anyway.
                    enough = diag.get("corr_n", 0) >= max(8, hdr.mcus_x // 2)
                    if (cand_path is not None
                            and diag.get("corr") is not None
                            and (not enough or diag["corr"] >= p.corr_threshold())):

                        # ---- periodic full-row verification --------------
                        #
                        # Deferring to the decoder is only safe while the
                        # decoder can actually tell. NIST's inter-fragment
                        # fill is Huffman-decodable, so it does NOT desync,
                        # and jump.jpg ran straight from its true fragment
                        # end at s13377 into fill at s13378 and onwards --
                        # every single block accepted, every one wrong.
                        #
                        # So once a full MCU row has accumulated since the
                        # last verified point, check it: a row is enough
                        # samples to judge honestly. If it fails, the run
                        # went wrong somewhere inside it, so rewind to the
                        # last verified checkpoint and make that path branch
                        # instead of running on.
                        # DISABLED -- measured, and it cannot work as designed.
                        #
                        # On image-frag-jpg.dd the fill between fragments is
                        # photometrically indistinguishable from real image
                        # data. Full-row seam correlations, jump.jpg:
                        #
                        #     true path : median 0.917, min 0.260 (n=213)
                        #     fill run  : median 0.890, min 0.846 (n=12)
                        #
                        # The fill scores BETTER than 2% of genuine rows. No
                        # threshold separates them, so rewinding on a low
                        # correlation only ever discards correct runs: with it
                        # enabled dino.jpg fell from 47,626 of 47,628 MCUs to
                        # 2,097. This is a limit of the hand-written DC
                        # discriminator, not a tuning problem.
                        #
                        # And NOT an argument for a learned replacement: the
                        # AUC experiment measured learned adjacency at 0.563 on
                        # JPEG entropy-stream data, below a byte-histogram
                        # baseline. For compressed formats the decoder and the
                        # global constraints are the whole contribution.
                        verified = cand_path
                        if False and verified.mcus - verified.verify_seam >= hdr.mcus_x:
                            cc, corr_v, n_v = J.dc_seam_correlation(
                                verified.dc, hdr.mcus_x, verified.verify_seam,
                                verified.dc_base)
                            if n_v >= 8 and corr_v < p.corr_threshold():
                                back = verified.checkpoint
                                if back is not None and back is not verified:
                                    back = replace(back, force_branch=True)
                                    children.append(back)
                                    expansions.append(diag)
                                    continue
                            else:
                                verified = replace(verified,
                                                   verify_seam=verified.mcus)
                                verified.checkpoint = verified

                        children.append(verified)
                        expansions.append(diag)
                        n_runs += 1
                        continue

                # ---- two-stage: cheap sweep, then probe the shortlist ----
                #
                # Scoring a candidate on the single block it contributes is
                # too thin to decide with. On the NIST corpus one 512 B sector
                # yields ~17 MCUs against a 162-to-252 wide MCU row, and with
                # up to 2048 candidates in flight the best-of-N noise reliably
                # beats the true successor: dino.jpg assembled 47,627 of
                # 47,628 MCUs through eight wrong fragments instead of two.
                #
                # So the cheap correlation only RANKS. The shortlist is then
                # re-scored by extending each candidate far enough to cover a
                # full MCU row, which is the smallest unit that carries real
                # photometric evidence. Same retrieve-then-rerank shape the
                # Phase 1 bi-encoder/cross-encoder will use.
                shortlist = []
                for cand, lp in self.candidates_for(p):
                    nxt, diag = self.extend(hdr, p, cand, lp)
                    n_decode += 1
                    n_scored += 1
                    if nxt is not None:
                        # The single-sector decision above is 8x shallower than
                        # the window the DFRWS-2024 guarantee is stated for, so
                        # confirm the candidate at that depth before it is
                        # allowed to compete. This is a HARD constraint: it
                        # prunes, it does not down-weight.
                        feasible, surv, why = (self.deep_validate(hdr, nxt)
                                               if self.deep_validation
                                               else (True, 0, "disabled"))
                        n_decode += surv
                        diag["validated_sectors"] = surv
                        if not feasible:
                            diag["kept"] = False
                            diag["reason"] = ("failed 4KB validation after %d "
                                              "sectors (%s)" % (surv, why))
                            nxt = None
                    expansions.append(diag)
                    if nxt is not None:
                        shortlist.append((diag["delta"], cand, lp, nxt))

                shortlist.sort(key=lambda t: -t[0])
                for _d, cand, lp, nxt in shortlist[: self.probe_top_k]:
                    probed, gained = self.probe(hdr, p, nxt, cand, lp)
                    n_decode += gained
                    children.append(probed)

            if not children:
                break

            # de-duplicate identical cluster sets, keep the best scorer
            best = {}
            for c in children:
                k = c.key()
                if k not in best or c.score > best[k].score:
                    best[k] = c
            children = sorted(best.values(), key=lambda c: -c.score)
            if children:
                cutoff = children[0].score - SCORE_MARGIN
                children = [c for c in children if c.score >= cutoff]
            beam = children[: self.beam_width]

            if self.collect_trace:
                entry = {
                    "step": step,
                    "mode": "run" if n_runs == len(beam) else "expand",
                    "from": [p.clusters[-1] for p in beam],
                    "expansions": sorted(expansions,
                                         key=lambda d: (-(d["delta"] or -1e9)))[:24],
                    "beam": [{"clusters": c.clusters, "score": round(c.score, 2),
                              "mcus": c.mcus} for c in beam],
                }
                trace.append(entry)
                if self.on_step:
                    self.on_step(entry)

        elapsed = time.time() - t0

        if not finished:
            best = max(beam, key=lambda p: p.score) if beam else None
            return CarveResult(
                False, best.clusters if best else start_clusters, b"",
                best.mcus if best else 0, hdr.total_mcus,
                best.score if best else 0.0, 0.0,
                _fragments(best.clusters) if best else 0,
                elapsed, n_scored, n_decode, trace,
                "no path decoded all %d MCUs" % hdr.total_mcus)

        finished.sort(key=lambda p: -p.score)
        win = finished[0]
        margin = (win.score - finished[1].score) if len(finished) > 1 else float("inf")

        # Rebuild the whole file from its clusters -- `buf` is only a window
        # now, so it no longer contains the earlier fragments.
        data = view.concat(win.clusters)
        abs_bp = win.buf_base + win.state.bp
        eoi = data.find(J.EOI_BYTES, max(0, abs_bp - 4))
        if eoi < 0:
            eoi = data.rfind(J.EOI_BYTES)
        data = data[: eoi + 2] if eoi >= 0 else data

        return CarveResult(True, win.clusters, data, win.mcus, hdr.total_mcus,
                           win.score, margin, _fragments(win.clusters),
                           elapsed, n_scored, n_decode, trace, "complete")
