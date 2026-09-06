"""
Walk the GROUND-TRUTH cluster order and report what the validator thinks of
each step, then show what the search preferred instead.

This separates the two failure modes that look identical from the outside:
  - the true path is REJECTED by a hard constraint  -> validator bug
  - the true path is accepted but OUTSCORED         -> scoring/search bug

Diagnostic only. Nothing here runs at carve time.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carve.beam import BeamCarver
from carve.blocks import ClusterView
from carve.priors.base import get_prior
from carve.validators import jpeg as J


def main() -> int:
    image = sys.argv[1] if len(sys.argv) > 1 else "corpus/images/easy.img"
    manifest = image.replace(".img", ".manifest.json")
    with open(manifest) as f:
        man = json.load(f)

    view = ClusterView(image, man["cluster_size"])
    prior = get_prior("locality")
    carver = BeamCarver(view, prior, collect_trace=False)

    for tf in man["files"]:
        order = tf["cluster_order"]
        print("\n=== %s : %d clusters, %d fragments ==="
              % (tf["name"], len(order), tf["n_fragments"]))
        print("true order: %s" % order)

        hdr = J.parse_header(view.cluster(order[0]), 0)
        nhc = max(1, (hdr.scan_start + view.cluster_size - 1) // view.cluster_size)
        path_clusters = order[:nhc]
        buf = view.concat(path_clusters)
        dc = []
        res = J.decode_mcus(hdr, buf, J.DecodeState.initial(hdr, hdr.scan_start), dc_out=dc)
        print("header: %s -> %s, %d MCUs" % (hdr.describe(), res.status, res.state.mcu_index))

        from carve.beam import Path
        p = Path(clusters=list(path_clusters), buf=buf, state=res.state, dc=dc,
                 score=0.0, status=res.status)

        for c in order[nhc:]:
            gap = c - p.clusters[-1]
            lp = prior.log_gap_prob(gap)
            nxt, diag = carver.extend(hdr, p, c, lp)
            tag = "OK " if nxt is not None else "REJ"
            print("  %s c%-5d gap=%+4d  status=%-9s mcus+=%-5d corr=%-7s "
                  "prior=%-7.2f delta=%-8s %s"
                  % (tag, c, gap, diag["status"], diag["mcus_gained"],
                     diag.get("corr"), lp, diag["delta"], diag["reason"]))
            if nxt is None:
                print("      >>> TRUE PATH REJECTED HERE -- validator problem")
                break
            p = nxt
        else:
            print("  true path is FEASIBLE end-to-end, final score=%.2f, status=%s"
                  % (p.score, p.status))

            # what did the search prefer at each fragment boundary?
            print("  -- competition at fragment boundaries --")
            p2 = Path(clusters=list(path_clusters), buf=view.concat(path_clusters),
                      state=res.state, dc=list(dc), score=0.0, status=res.status)
            for i, c in enumerate(order[nhc:]):
                gap = c - p2.clusters[-1]
                if gap != 1:
                    ranked = carver.candidates_for(p2)
                    scored = []
                    for cand, lp2 in ranked:
                        nx, dg = carver.extend(hdr, p2, cand, lp2)
                        if nx is not None:
                            scored.append((dg["delta"], cand, dg))
                    scored.sort(reverse=True)
                    print("     at c%d, true next = c%d (gap %+d):"
                          % (p2.clusters[-1], c, gap))
                    for delta, cand, dg in scored[:6]:
                        mark = " <== TRUE" if cand == c else ""
                        print("        c%-5d gap=%+4d delta=%-8s corr=%-7s mcus+=%d%s"
                              % (cand, dg["gap"], delta, dg.get("corr"),
                                 dg["mcus_gained"], mark))
                    if not any(cand == c for _, cand, _ in scored):
                        print("        !! true successor not in candidate set")
                nx, _ = carver.extend(hdr, p2, c, prior.log_gap_prob(gap))
                if nx is None:
                    break
                p2 = nx
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
