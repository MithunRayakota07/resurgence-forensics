"""
Does the wrong path STEAL clusters from other files, or does it eat filler?

This decides whether joint multi-file assembly (hypothesis H1 in CLAUDE.md 5b)
can possibly help. If a wrong assembly is built mostly from clusters that
genuinely belong to OTHER files in the image, then a constraint saying "every
cluster belongs to at most one file" makes that assembly unavailable rather
than merely lower-scoring, and H1 is worth building.

If the wrong assembly is mostly unowned filler, no exclusion constraint can
touch it and H1 is dead.

Diagnostic only.
"""
import json, os, sys, time

sys.path.insert(0, r"C:\dev\sih-forensics")

from carve.beam import BeamCarver
from carve.blocks import ClusterView
from carve.priors.base import get_prior

MAN = r"C:\dev\sih-forensics\corpus\cfreds\image-frag-jpg.manifest.json"
IMG = r"C:\dev\sih-forensics\corpus\cfreds\images\image-frag-jpg.dd"

man = json.load(open(MAN))
cs = man["cluster_size"]

# cluster -> owning filename, from ground truth
owner = {}
for f in man["files"]:
    for c in f["cluster_order"]:
        owner[c] = f["name"]

print("image: %s  cluster size %d  total %d clusters"
      % (os.path.basename(IMG), cs, man["total_clusters"]))
print("ground truth covers %d clusters across %d files\n"
      % (len(owner), len(man["files"])))

view = ClusterView(IMG, cs)
prior = get_prior("locality")

targets = sys.argv[1:] or ["jump.jpg"]

for name in targets:
    tf = next(f for f in man["files"] if f["name"] == name)
    truth = tf["cluster_order"]
    truth_set = set(truth)
    start = truth[0]

    print("=== %s ===" % name)
    print("true: %d clusters, %d fragments, starts at c%d"
          % (len(truth), tf["n_fragments"], start))

    t0 = time.time()
    carver = BeamCarver(view, prior, beam_width=8, collect_trace=False)
    r = carver.carve(start)
    el = time.time() - t0

    got = list(r.clusters)
    print("carved: ok=%s  %d clusters, %d fragments, %s/%s MCUs, %d candidates, %.0fs"
          % (r.ok, len(got), r.n_fragments, r.mcus, r.total_mcus,
             r.candidates_scored, el))
    print("reason: %s" % r.reason)

    if got == truth:
        print("  -> byte-exact path, nothing to analyse\n")
        continue

    # ownership breakdown of the clusters the search actually chose
    mine = [c for c in got if c in truth_set]
    other = [c for c in got if c in owner and c not in truth_set]
    filler = [c for c in got if c not in owner]

    n = len(got)
    print("\n  ownership of the %d clusters the search chose:" % n)
    print("    %-28s %5d  (%.1f%%)" % ("belong to this file", len(mine), 100.0 * len(mine) / n))
    print("    %-28s %5d  (%.1f%%)" % ("belong to ANOTHER file", len(other), 100.0 * len(other) / n))
    print("    %-28s %5d  (%.1f%%)" % ("unowned filler", len(filler), 100.0 * len(filler) / n))

    if other:
        from collections import Counter
        by = Counter(owner[c] for c in other)
        print("\n  stolen from:")
        for fn, k in by.most_common():
            print("    %-18s %d cluster(s)" % (fn, k))

    # where does it first diverge from the truth?
    for i, (a, b) in enumerate(zip(got, truth)):
        if a != b:
            print("\n  first divergence at step %d: chose c%d, truth was c%d" % (i, a, b))
            src = owner.get(a, "FILLER")
            print("    the cluster it chose belongs to: %s" % src)
            break
    print()
