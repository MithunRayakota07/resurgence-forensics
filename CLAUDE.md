# CLAUDE.md — Tessera project memory

This file is the project's memory. If you are a new teammate, or a fresh AI
session, read this before touching anything. It should give you the full
picture without anyone re-explaining it.

**Last updated:** 8 September 2026.

---

## 1. What this is

Three capabilities, built to work together on a raw disk image:

1. Drive-level sanitisation with verified audit trails (NIST SP 800-88r2)
2. Selective file/folder erasure with metadata scrubbing
3. Forensic file carving from formatted or corrupted media, with a
   confidence score

The project began as an entry for Smart India Hackathon 2026 (problem
statement SIH26149, NTRO) and the three-module shape is inherited from that
statement. **It is no longer a competition entry**, and the hackathon's
deadlines, team rules and effort quotas no longer constrain anything. See
`docs/ROADMAP.md` for what the project is aiming at now.

That history matters in one place only: the modules are three because a
problem statement said three, not because a lone designer chose it. Do not
treat the split as sacred if a better shape appears.

---

## 2. Where we are

**Phase 0 is complete and working.** No learned model yet — everything below
is a hand-written JPEG validator plus constrained beam search.

**All numbers below re-measured 24 Aug** on an idle machine (i7-12700H,
14C/20T, CPU at 0%, nothing else running), tools run sequentially so nothing
contends. The previous figures in this section were taken under unknown load
AND before the byte-scaled search window, the two-stage probe and the
QA-overflow check were added; they understated our runtime by ~6x and are
superseded. Do not quote 7.6 s or 16.1 s anywhere.

### `hard.img` — one JPEG, three fragments, out of order with a backward jump

| tool | byte-exact | time | result |
|---|---|---|---|
| **Tessera** | **1 / 1** | **67.5 s** | byte-exact |
| PhotoRec + brute force | 0 / 1 | 1.6 s | nothing recovered |
| PhotoRec (default) | 0 / 1 | 1.4 s | nothing recovered |
| Foremost | 0 / 1 | 0.9 s | corrupt, SSIM 0.59 |
| Scalpel | 0 / 1 | 1.5 s | corrupt, SSIM 0.59 |

### `easy.img` — two JPEGs interleaved, fragments in order

| tool | byte-exact | time |
|---|---|---|
| **Tessera** | **2 / 2** | **118.2 s** |
| PhotoRec + brute force | 1 / 2 | 257.9 s |
| PhotoRec (default) | 0 / 2 | 1.6 s |
| Foremost | 0 / 2 | 14.7 s |
| Scalpel | 0 / 2 | 0.8 s |

**Runtime variance is real and thermal.** Across five clean repetitions
hard.img ranged 49.9-75.7 s (median 67.5) and easy.img 95.1-143.7 s. It is a
laptop under sustained single-core load; later repetitions run ~50% slower than
the first. Quote the median and say it is a laptop.

**We are now the SLOWEST tool that works, by a wide margin** — roughly 45x
PhotoRec's default on hard.img. The honest framing: the fast tools return
nothing or return corrupt files, so the comparison that matters is against
PhotoRec + brute force, which is the only other tool that recovers anything
fragmented — and we still beat it 2/2 vs 1/2 on easy.img while being 2.2x
faster there. On hard.img nothing else recovers the file at any speed. But see
section 8: runtime at scale remains an unanswered line of attack, and it is now
a WORSE one than it was.

PhotoRec is always run **with** `paranoid_bf` brute-force mode, which exists
specifically for fragmented JPEGs. Running it without that switch would be
rigging the comparison and anyone who knows the tool would catch it in one
command. On `easy.img` it genuinely beats us on nothing but honesty — it gets
one file byte-exact. Say so.

### Ablation — what the allocation prior is actually worth

On `hard.img`, same idle machine:

| prior | result | MCUs | candidates | time |
|---|---|---|---|---|
| `locality` | byte-exact | 1900 / 1900 | 8,041 | **73.7 s** |
| `uniform` (control) | **never closes the file** | 1868 / 1900 | 158,452 | **1753.9 s** |

Without the prior the search explodes: 19.7x the candidates, **23.8x the
runtime**, and it still fails, ending on an 8-fragment assembly that reaches
1868 of 1900 MCUs. The old note in this section said the prior bought "roughly
2x the speed" — that was measured before the byte-scaled search window, and it
badly understated the effect. The prior buys accuracy *and* ~24x the speed.

---

## 3. The novelty claim — EXACT WORDING

Do not let anyone on the team overstate these. Each one has a published owner
and a judge may know them.

**Sequencing is NOT ours.** Pal & Memon (2006) formulated fragment reassembly
as a k-vertex-disjoint path / maximum-weight Hamiltonian path problem and
solved it greedily (PUP). Shanmugasundaram & Memon (2003) did it for documents.
Deepzzle (ECCV 2018), JigsawNet and DNN-Buddies did learned adjacency plus
shortest path — for image jigsaws.

**Out-of-order carving is NOT ours.** Huijsmans, Kuijsten, Jonker & van Beek,
*"How to Carve Out-of-Order Fragmented Files,"* LNCS 16365, Springer, **2026**.
They also built the tunable fragmentation corpus generator. **We position
against their stated open problem: efficiency in practical settings.**

**Allocation priors are NOT wholly ours.** Karresand, Dyrkolbotn & Axelsson
published three papers in 2019–2020 on NTFS cluster allocation behaviour,
explicitly naming file carving as an application. Our narrower true claim:

> They use allocation behaviour to prioritise **where** to search. We use it as
> a **fitted pairwise gap likelihood inside the sequencing objective**, with an
> ablation showing what it's worth.

**What IS ours — and it is SPLIT BY FORMAT. Do not state it unqualified.**

Nobody learns adjacency over raw binary disk fragments. Confirmed across open
source — FiFTy (classification only), FileScraper (Huffman table extraction),
JPEG-Restorer (syntactic + thumbnail affinity), JigsawNet and Deepzzle (pixels,
not bytes). None do it. But we only claim it where we measured it working:

| data | our contribution | measured |
|---|---|---|
| **Compressed** (JPEG, GIF) | validator + constrained search. **NO learned-model claim.** | JPEG entropy-stream **0.563**, GIF 0.476 — below a byte-histogram baseline |
| **Documents / spreadsheets** (DOC, XLS) | **learned adjacency** over raw binary | XLS **0.838**, DOC **0.799** vs near-negatives, both beating the baseline |

Decided 23 Aug after the AUC experiment (5c). **Nothing anywhere may say we
learn the edge-weight function for JPEG.** We measured it; it does not work on
entropy-coded data; claiming otherwise is the fastest way to lose a technical
judge who asks for the number.

Two caveats travel with the document figures, always: single seed, and DOC/XLS
are OLE compound files so part of the signal is probably container structure
rather than content adjacency.

**Supporting citation for `hard.img`:** van der Meer, Jonker & van den Bos,
*"A contemporary investigation of NTFS file fragmentation,"* FSI:DI, 2021 —
220 real Windows laptops; a significant portion of fragmented files are stored
**out of order**. This is the empirical justification for the hard case; it is
far stronger than us asserting it.

---

## 4. The evidence that makes us credible

NIST/CFTT (the US government's Computer Forensics Tool Testing programme)
publishes per-tool results on a standard fragmented-carving benchmark, released
through DHS. On **fragmented JPG**, every one of these carved files that
**included the fill between fragments** — i.e. the garbage between fragments is
left inside the output file:

| tool | version | tested |
|---|---|---|
| Magnet AXIOM | 7.5.0.37231 | Jul 2024 |
| FTK | 8.0 | Jan 2024 |
| Autopsy | 4.21.0 | Feb 2024 |
| R-Studio T80+ | 9.3.191230 | Jul 2024 |
| Forensic Explorer | 5.6.8.4340 | Mar 2025 |

**X-Ways** documents its own limitation: carving "generally assumes contiguous
file clusters, so it produces corrupt files in case the files were originally
stored in a fragmented way."

**EnCase** 7.09.05: 10 of 40 viewable-complete, plus **9,054 false-positive
BMPs**.

**Not one of them reassembles.** And note what the CFTT benchmark does *not*
cover: every layout is fragmented **in order**. Out-of-order is untested by the
government benchmark entirely.

---

## 5. Known threats — every team member must be able to answer these

**1. TRIM.** *"Everything's an SSD, nothing's left to carve."*

**Use this wording. An earlier draft of this answer contained a false claim —
"TRIM never fires on a reformat" — which is wrong: Windows has issued TRIM on
quick format of SSDs since Windows 8. Saying that on stage in front of anyone
who knows storage loses the room on the one slide meant to save it.**

Corrected answer:

> TRIM does not reach SD and camera media, most USB enclosures (no UNMAP
> pass-through), or the magnetic drives still widely deployed in government,
> NAS and CCTV estates. And TRIM never fires on **corruption** — a lost
> partition table, filesystem structure damage, or ransomware leaves the data
> physically intact with only the metadata destroyed. Carving from formatted
> or corrupted media is a metadata problem, not a deletion problem.

Lead with the **corruption** argument; it is the strongest, and it is the case
where carving is the only option left.

Do **not** claim phones — mobile UFS/eMMC use discard and are largely out of
scope. Do **not** claim reformat. "Magnetic drives common in Indian government
estates" is currently an unsourced assertion; either find a citation or keep the
softer "still widely deployed in government, NAS and CCTV estates."

**2. "16% of one file type."**
Own it: we add the hard part; the easy part was already solved. The base rate
is the wrong statistic because carving only runs when metadata is already gone.

**3. Adroit Photo Forensics** (Digital Assembly) — commercialised SmartCarving,
claimed ~99% on fragmented JPEGs. **RESOLVED 23 Aug: no longer obtainable.**
`digital-assembly.com` now serves "WeRecoverData" content, and every path --
including deliberately nonsensical ones -- returns an identical 6,701-byte
catch-all page. The vendor no longer distributes it. Third-party mirrors
(sharewarejunction, software.informer, soft32) exist but must NOT be used:
downloading forensic binaries from mirror sites is a known malware vector.

Say it this way: *"The only commercial tool that ever claimed fragmented JPEG
reassembly was Adroit, the commercialisation of Pal & Memon's SmartCarving. We
tried to obtain it; the vendor no longer distributes it. So that capability is
not commercially available today."* Do NOT claim we benchmarked against it.

**4. Our test images are self-generated.** ~~Softest part of our evidence.~~
**Largely closed 8 Sept — see 5g.** `tessera-gen-real` builds the same layouts
from real photographs with other real photographs as decoy filler, and 54 of 54
randomised out-of-order layouts recover byte-exact. What remains open: one
camera, one resolution, one layout shape, and fragmentation chosen by our
harness rather than by real filesystem ageing.

**5. No learned component exists yet.** All current results are a hand-written
JPEG validator plus beam search.

---

## 5b. The NIST result (23 Aug) — read this before touching the carver

**Tessera scores 0 / 6 byte-exact on the NIST CFReDS fragmented-JPG image.**
Our own images still pass byte-exact. Both facts matter.

**Update 23 Aug (second attempt).** Still 0/6, but the failure changed shape and
two new facts are now established.

*The fill was plain text all along.* CFReDS inter-fragment fill is the ASCII
string "*** FILL TEXT BLOCK ***" repeated: entropy 3.20 against 7.54 for real
scan data, 100% printable, zero 0xFF bytes in 855 sectors. Our entropy floor
would have rejected it, but RUN-MERGE BYPASSED THE CANDIDATE FILTERS. That one
oversight caused three failed fix attempts. `J.plausible_scan_data()` now runs
as a hard precondition on every candidate including sequential ones:
22,720/22,720 true clusters accepted, 0/855 fill accepted.

*The global constraint is NOT sufficient.* jump.jpg and oak-snow.jpg now decode
their exact MCU count AND land on EOI via demonstrably WRONG assemblies (7 and
8 fragments instead of 2). **Do not build a search that assumes only the true
path can complete** -- that was the plan and it is invalid. A globally
consistent reconstruction can still be wrong.

With fill excluded the competitors are other photos' real JPEG data, which
decodes validly and looks photometrically plausible. That is the same
discrimination the AUC experiment measured at 0.563 for JPEG. Two independent
measurements now agree that the hand-written approach has a ceiling here.

Honest caveat to volunteer before a judge raises it: our fill detection works
because NIST's fill is TEXT. Real-world inter-fragment data is other files,
which may be another JPEG. The precondition makes no claim about that case,
and that case is what the six failures are.

**All six true paths ARE feasible**: walking the ground-truth cluster order
decodes every file to its exact MCU count and lands on EOI, in 1.3-8.7 s. The
decoder and the derived ground truth are both correct. What fails is the
search.

**And the reason the search fails is fundamental, not a bug.** On this corpus
the inter-fragment fill is photometrically indistinguishable from real image
data. Measured full-row seam correlations on jump.jpg:

| | median | min |
|---|---|---|
| true path (n=213) | 0.917 | 0.260 |
| **fill run (n=12)** | **0.890** | **0.846** |

The fill scores better than 2% of genuine image rows. **No threshold separates
them.** A verification-and-rewind scheme built on this signal made things
strictly worse -- dino.jpg fell from 47,626 of 47,628 MCUs to 2,097 -- and is
disabled in `carve/beam.py` with the numbers recorded inline.

**What this means for the pitch.** Our discriminator is a hand-written
DC-continuity heuristic and it does not generalise past images we generated
ourselves. That is an honest limitation.

**It is NOT an argument for the learned model.** An earlier draft of this
section said the NIST failure was "exactly the gap a learned adjacency function
exists to fill". Section 5c then measured that gap and the model does not fill
it: JPEG entropy-stream AUC 0.563, below a byte-histogram baseline, under both
negative-sampling regimes. Two independent measurements now agree.

So for compressed formats the honest position is: **the decoder and the global
constraints do the work, and there is no learned component to claim.** The
learned contribution lives on documents and spreadsheets (see 5c). Do not
repair this story by promising a model; the measurement is against us here.

Do NOT quote a CFReDS number in our favour. Quote the 0/6, quote that the true
paths are reachable, and quote the fill measurement.

## 5c. The AUC experiment (23 Aug) — what the learned model can and cannot do

Ran on **govdocs1** (1,959 real government documents, zips 000+001), negatives
always **same-file**, split **by file**, ~18k pairs for the larger formats.
Decision rule was fixed before the run. Full results in
`bench/out/auc_results.json`, code in `model/auc_experiment.py`.

| format | histogram | bi-encoder | **cross-encoder** | verdict |
|---|---|---|---|---|
| .doc | 0.752 | **0.865** | 0.863 | **WORKS** |
| .xls | 0.778 | 0.730 | **0.850** | **WORKS** |
| .ppt | 0.742 | 0.659 | 0.729 | below baseline |
| .ps | 0.746 | 0.566 | 0.556 | below baseline |
| .pdf | 0.688 | 0.580 | 0.655 | below baseline |
| .html | 0.663 | 0.547 | 0.573 | below baseline |
| .txt | 0.659 | 0.499 | 0.546 | below baseline |
| **.jpg entropy-stream** | 0.654 | 0.513 | **0.559** | **DOES NOT WORK** |
| .jpg (whole file) | 0.643 | 0.517 | 0.570 | DOES NOT WORK |
| .gif | 0.616 | 0.494 | 0.508 | DOES NOT WORK |

**Three conclusions, and they redirect the project.**

1. **Learned adjacency does NOT work on compressed data.** JPEG entropy-stream
   0.559, GIF 0.508 — at or near chance, and below a byte-histogram baseline.
   This was the pre-registered "< 0.60 → route by entropy" branch. Combined
   with 5b (DC continuity cannot separate NIST's fill from real image data),
   **the JPEG story cannot be rescued by the learned model.** For compressed
   formats the decoder and the global constraints do the work; ML does not.

2. **It DOES work on structured/office formats** — DOC 0.865, XLS 0.850, both
   beating the baseline. That is where the learned contribution is real, and
   conveniently it is also where forensically interesting content lives
   (documents, spreadsheets, and by extension SQLite and mail stores).

3. **The seam cross-encoder is the right architecture** and the bi-encoder
   alone is not. Cross beats bi on almost every format (xls 0.850 vs 0.730,
   ppt 0.729 vs 0.659, pdf 0.655 vs 0.580). The bi-encoder stays as the
   FAISS-indexable retriever; the cross-encoder is the reranker.

**Always report the histogram baseline.** Cosine over 256-bin byte histograms
scores 0.62-0.78 with no learning at all, and beats our model on 8 of 10
formats. No FFT-75 paper we have seen reports such a control. Any AUC we quote
without it is not evidence.

**The near-negative (operational) run — and it changed the conclusion.**

Negatives restricted to blocks flanking the true successor (+/-20), which is
the decision the search actually faces. `--max-gap 20`, results in
`bench/out/auc_near.json`.

| format | histogram far -> NEAR | cross-encoder far -> NEAR | margin NEAR |
|---|---|---|---|
| .xls | 0.778 -> 0.705 | 0.850 -> **0.838** | **+0.133** |
| .doc | 0.752 -> 0.685 | 0.863 -> **0.799** | **+0.114** |
| .ppt | 0.742 -> 0.647 | 0.729 -> 0.695 | **+0.048** |
| .pdf | 0.688 -> 0.618 | 0.655 -> 0.633 | **+0.016** |
| .jpg entropy | 0.654 -> 0.619 | 0.559 -> 0.563 | -0.056 |
| .txt | 0.659 -> 0.624 | 0.546 -> 0.546 | -0.079 |
| .gif | 0.616 -> 0.561 | 0.508 -> 0.476 | -0.085 |

**The harder experiment is BETTER for us, which is unusual and worth saying
out loud.** Mean drop from far to near negatives: histogram **-0.062**,
cross-encoder **-0.022**. The baseline was leaning on REGIONAL similarity
(same chapter, same sheet) which near-negatives strip out; the model was
learning something closer to real adjacency, so it barely moved. PPT and PDF
flip from losing to the baseline to beating it.

Quote the NEAR numbers, not the far ones. They are both harder and kinder.

**Settled conclusions.**
* Compressed data (JPEG entropy 0.563, GIF 0.476) fails under BOTH regimes.
  Not rescuable by this model. Decoder + global constraints only.
* Office/structured formats work, and the margin grows under realistic
  difficulty. XLS and DOC are the demo formats for the learned model.
* Plain text and HTML fail our byte-CNN (0.546/0.554, below baseline). Text
  adjacency probably needs a language model, not a conv stack -- an open
  question, not a closed door.

## 5d. Three dead-end probes (24 Aug) — do not re-run without new reason

Asked whether anything could move the carver off 0/6 on CFReDS. Measured, not
guessed:

* **MFT/filesystem metadata on CFReDS: none exists.** The .dd images are raw
  file layouts in unallocated space (they begin with an ASCII "Image:" header),
  not formatted volumes. Zero MFT records. No size/start-cluster data to exploit.
* **Re-weighting the allocation prior cannot flip it.** On jump.jpg the wrong
  7-fragment completion beats the true 2-fragment path on BOTH the seam-corr
  term (2968 vs 2985) and the prior term (11906 vs 11781). Raising W_PRIOR
  1.5->40 widens the gap the wrong way. The decoy is prior-friendlier than the
  truth. W_PRIOR stays at 1.5.
* **Thumbnail-guided scoring: real idea, wrong project.** Using the embedded
  EXIF thumbnail as an ABSOLUTE reference is the one angle that could beat the
  relative seam ceiling -- but it is published (Abdullah, Ibrahim & Mohamad
  2013; and a 2024 DFRWS "Problem solved" deterministic-JPEG paper we have NOT
  read), only 3 of 6 CFReDS files even have a thumbnail, and a quick probe was
  confounded by EXIF-rotation (portrait image, landscape thumbnail). Parked for
  Phase 2 with the citation; not built, per the no-new-contributions boundary.

## 5e. QA-overflow validator (24 Aug) — a real decoder bug, with a citation

The carver failed on CFReDS not (only) because scoring is weak, but because our
HARD CONSTRAINT had a hole. Our AC-coefficient loop checked the normal run
path for quantization-array overflow (`k += run; if k > 63`) but the ZRL path
(`k += 16`) had NO bounds check. A block already near full would run past
coefficient 63 and the `while k < 64` test just exited as if the block ended
cleanly -- swallowing an overflow that proves the bytes are NOT a valid JPEG
continuation. Fixed in carve/validators/jpeg.py: `if k + 16 > 64: raise Desync`.

This is exactly the QA-overflow validation of **van der Meer, van den Bos,
Jonker & Dassen, "Problem solved: A reliable, deterministic method for JPEG
fragmentation point detection", DFRWS EU 2024** (FSI:DI 48, 301687). Their
result: bit-level Huffman + QA-overflow checks invalidate a wrong extension
with >99.4% probability within 4 KB (99.99% for baseline JPEG). They consider
fragmentation-point DETECTION solved; they explicitly leave multi-fragment
out-of-order REASSEMBLY as ongoing work -- which is our lane.

CITE THEM for the validator. Do not present QA-overflow as ours; present the
correctly-composed pipeline (their feasibility check + our fitted allocation
prior + global-image-plausibility rerank) aimed at their stated open problem
(efficiency of out-of-order reassembly) as the contribution.

The decoder self-test still passes and all three synthetic images stay
byte-exact: a correct QA-overflow check never fires on valid data, so it is
strictly a tightening of the hard constraint, not a behavioural change on
good input.

## 5f. The validator-scope result (24 Aug) — our first real finding

We implemented per-candidate validation at the DFRWS-2024 window (4096 B,
`BeamCarver.deep_validate`) expecting it to make wrong candidates infeasible.
It did not: at jump.jpg's true fragmentation point it rejected only 71 of 1661
candidates (4%); 1590 survived the FULL 4 KB window and the true successor
ranked 87th. Option A does not fix 0/6.

Investigating why produced the finding. Their section 5.3 states the
experimental assumption verbatim: **"we generate and inject random data after
the fragmentation point"**, justified by "compressed data ... would resemble
random data". In MULTI-FILE carving that assumption fails -- the competing
bytes are another JPEG's entropy stream, encoded with the SAME standard
Huffman tables, so they decode cleanly.

Measured, same validator, same 4096 B window, only the competitor changed
(strict: every competitor supplies a full 4 KB of contiguous data;
`bench/out/validator_scope2.txt`):

| competitor | rejected within 4 KB |
|---|---|
| random data (their experiment) | **300/300 = 100%** |
| real JPEG data from another file on the same disk | **373/720 = 51.8%** |

The first row REPRODUCES their >99.4% claim -- our validator is correct and
behaves as published. The second shows the guarantee does not transfer to the
setting carving actually faces.

**And the split is structural, not random.** Per-file rejection against real
JPEG competitors:

| file | restart interval | rejected |
|---|---|---|
| dino.jpg | 252 | **100%** |
| grizzly.jpg | 252 | **100%** |
| jump.jpg | none | 27.5% |
| leaf.jpg | none | 19.2% |
| oak-snow.jpg | none | 25.8% |
| stonehenge.jpg | none | 38.3% |

Restart markers (FFD0-FFD7, carrying a sequence number) are a CHECKABLE
STRUCTURE: foreign data does not carry the right marker at the right offset,
so the validator rejects it deterministically. Without them the entropy stream
is self-synchronising and foreign JPEG data decodes cleanly. All six files are
baseline 2x2/1x1/1x1; the ONLY structural difference is the restart interval.

**The honest claim, and do not overstate it:**

> Fragmentation-point DETECTION is solved (van der Meer et al. 2024), and we
> reproduce their result exactly against random data. Fragmentation-point
> DISCRIMINATION AMONG REAL CANDIDATES is not solved: when the competitor is
> another JPEG rather than random data, rejection within 4 KB falls from 100%
> to 51.8%, and to 19-38% for files without restart markers.

Caveats that travel with it: six files, one corpus, one encoder family; the
restart-marker explanation is inferred from a perfect correlation across 6
files (n=2 with markers), not from a controlled experiment where we add or
remove markers. Do not call it proven until that experiment is run.

Note dino and grizzly still FAIL to carve despite 100% foreign rejection, so
their failure has a different cause, not yet isolated. Do not claim restart
markers would fix the carve.

**The gate is implemented but DISABLED by default** (`BeamCarver(...,
deep_validation=True)` to enable). It costs 4.2x runtime -- hard.img 20.3 s ->
85.1 s -- to prune 4% of candidates, and changes no outcome on either corpus.
hard.img stays byte-exact with it on AND off, with an identical 8041-candidate
search, so this is a pure cost/benefit call, not a correctness one. Keep the
code: it produced 5f, and it is the correct gate for a corpus whose filler is
random or non-JPEG rather than other photographs.

**Timings: RESOLVED 24 Aug.** Section 2, README and GUIDE have all been
re-measured on an idle machine and updated. hard.img is 67.5 s, not 7.6 s.

## 5g. Real photographs, and a success RATE (8 Sept) — threat 4 largely closed

Section 5 lists "our test images are self-generated" as threat 4 and the
softest part of the evidence. `corpus/generate/real_photos.py` addresses it:
it lays REAL photographs out on a disk image and uses slices of OTHER real
photographs — same camera, same encoder, same settings — as the surrounding
decoy filler. That is the maximally confusable case, and the one 5f measures
as unsolved when the competitor is another JPEG.

**First result, 20-photo webcam corpus:** 3/3 byte-exact with correct cluster
order and fragment count, including the out-of-order layout with a backward
jump (86.2 s, 45.6 s, 128.9 s).

**Then a rate, because 3 files is not a rate.** `bench/success_rate.py`
randomises which photograph is the evidence, both fragmentation points, the
start cluster, the backward-jump distance, the forward gap, and the filler
seed, then scores each byte-exact.

| corpus | image | instances | byte-exact | median carve |
|---|---|---|---|---|
| real webcam photos | 16 MiB | 30 | **30 / 30** | 39 s |
| synthetic photos | 16 MiB | 12 | **12 / 12** | 57 s |
| synthetic photos | 8 MiB | 12 | **12 / 12** | 57 s |

**54 of 54 randomised out-of-order layouts recovered byte-exact.** That is a
much stronger statement than the two hand-placed images in section 2, and it
is the number to quote.

**But there is one reproducible failure, and it is NOT explained.** The pytest
fixture in `tests/test_real_photo_corpus.py` builds a layout that fails:
900x700 synthetic evidence, `build_hard(..., total_clusters=2048, seed=1)`,
five decoys. The search reaches 2503 of 2508 MCUs via a wrong 11-fragment
assembly instead of the true 3-fragment path — the same failure shape as 5b.
It reproduces deterministically.

Three attempts to isolate the cause, all negative:

1. **File size and decoy density, factorially.** 800x600 and 900x700 evidence
   crossed with sparse synthetic filler and dense real-photo filler. All four
   combinations pass. Neither variable explains it.
2. **Image content.** If procedural imagery were the problem (section 6 records
   1.4x separation for noise against 3.4x for photographic statistics), the
   synthetic corpus should fail more. It does not — 12/12.
3. **Image size.** 8 MiB against 16 MiB, same layouts. Not only 12/12, but the
   candidate counts are IDENTICAL between the two runs (18085, 16209, 15495,
   ...). The search is byte-scaled and local, so total image size does not
   enter into it at all.

So the honest position: on randomised layouts of this shape the carver is
reliable, 54/54. A rare failure mode exists, it is reproducible, and its cause
is unknown. Do not describe the success rate as 100% without saying that a
known failing configuration is checked in.

**Incidental finding, relevant to section 8.** Point 3 above shows per-file
search cost is INDEPENDENT of image size — identical candidate counts on an 8
MiB and a 16 MiB image. That does not settle runtime at scale (header scanning
and the number of files both still scale), but it removes one of the reasons
to fear it, and it means the naive "67.5 s per 16 MiB, therefore 100 days per
2 TB" extrapolation is definitely wrong.

**What is still NOT closed.** One camera, one resolution, one layout shape, and
fragmentation chosen by our harness rather than produced by a real filesystem
ageing. CFReDS is still 0/6, and nothing here changes that — what differs there
is the layout, not the photographs.

## 6. Design lessons already learned the hard way — do not regress these

Each of these came from a test that failed, and each is preserved as a comment
at the relevant place in the code. If you find yourself "simplifying" one of
these, read the comment first.

- **Huffman validity is a weak discriminator.** Foreign fragments decode as
  valid symbols for ~90 MCUs, because JPEGs from the same encoder share the
  standard Huffman tables.
- **Absolute |ΔDC| is actively harmful.** DC is differentially coded, so a
  foreign fragment inherits the predecessor's predictor as a constant offset;
  flat, low-detail clusters score near-zero difference regardless of origin.
  Beam search found this exploit immediately and filled files with flat
  garbage. Use **Pearson correlation across the seam** instead — offset-free
  and scale-free.
- **`W_PRIOR = 6.0` let the prior overrule the evidence** and walked the search
  straight past a fragment boundary into filler. It is **1.5, tie-breaker
  only**. Never raise it without running the ablation.
- **MCU count alone is not a terminator.** Huffman self-synchronises, so a
  wrong cluster chain decodes 1900 valid-looking MCUs and stops on the counter.
  The scan must actually **end at EOI**.
- **Test corpora need photographic images.** Procedural noise gave 1.4×
  correct-vs-foreign separation; real photographic statistics gave 3.4×.

---

## 7. Hard rules

- **NEVER emit generated or synthesised bytes into an exported evidence file.**
  Every byte in output comes from the disk. Generation may score and prune
  candidates only. Not watermarked, not flagged, not opt-in — that is evidence
  fabrication and it disqualifies the entire tool.
- **The confidence number is UNCALIBRATED** and must be labelled as such
  everywhere it appears. It is not a probability until Phase 2.
- **Safety:** loopback images only. Never a physical device. Refuse the system
  disk. Typed serial confirmation required before any destructive operation.
- **NIST SP 800-88 Rev. 2** (Sept 2025). Rev. 1 was withdrawn the same day —
  do not cite it.
- **No blockchain.** Signed Ed25519 hash chain instead. Rationale: blockchain
  only adds value across mutually distrusting parties; a sanitization
  certificate has exactly one issuer. Blockchain-anchored wipe certificates
  are a recurring proposal in this space and they read as decoration. Keep
  this rationale to hand — it gets asked.
- **Keep the three capabilities roughly balanced.** The carver is the
  interesting one and will happily absorb all available time. Three solid
  capabilities are worth more than one brilliant one beside two stubs.
- **Split train/test by source file and by corpus, never by block.** Blocks
  from the same file leaking across the split will hand you a fake 99%.
- **SHA-256 ground-truth comparison is evaluation-only, never a runtime
  signal.** It lives in `bench/score.py` and nowhere else. In a real case there
  is no original, so a carver that consults one is measuring nothing.

---

## 8. Open questions

- ~~Compressed-data AUC experiment~~ — **RUN 23 Aug, see 5c.** Answer: learned
  adjacency does NOT work on entropy-coded data (JPEG 0.563, GIF 0.476) and
  DOES work on documents/spreadsheets (XLS 0.838, DOC 0.799). The claim is now
  split by format; see section 3. Remaining work: repeat with 3-5 seeds for
  error bars, and probe whether the DOC/XLS signal is content adjacency or OLE
  container structure.
- **Publishing** (arXiv / DFRWS / FSI:DI) undecided.
- **Runtime at scale unmeasured, and the exposure GREW.** Re-measured 24 Aug:
  **67.5 s** on a 16 MiB image, not the 7.6 s previously recorded. Naively that
  is ~100 days for a 2 TB drive. **Partly addressed 8 Sept (see 5g):** the
  per-file search cost does not depend on image size at all — an 8 MiB and a
  16 MiB image produce byte-identical candidate counts, because the search
  window is byte-scaled and local. So the naive per-byte extrapolation is
  definitely wrong. What still scales is the header scan and the number of
  files found, and neither has been measured. Running the carver over a 1 GB
  image remains the cheapest way to close this properly.
- ~~TRIM rebuttal unchecked~~ — done 23 Aug; one clause was false and is corrected in section 5.

---

## 9. Running it from cold

**The carver alone needs only Python 3.11+.** It is a proper installable
package (`pyproject.toml`), and core dependencies are NumPy and Pillow:

```bash
pip install -e .
tessera-carve corpus/images/hard.img --out recovered/
```

Console entry points: `tessera-carve`, `tessera-erase-drive`,
`tessera-erase-metadata`, `tessera-erase-residue`, `tessera-gen-corpus`,
`tessera-bench`. FastAPI/uvicorn are the `[api]` extra and pytest is `[dev]`, so
a plain install does not drag in a web server.

**Tests.** `pip install -e ".[dev]"`, then:

```bash
pytest -m "not slow"     # fast: decoder, priors, blocks, erase safety, certs
pytest                   # adds the end-to-end byte-exact carves
```

The slow marker covers the real-disk-image carves; they skip themselves if
`corpus/images/` has not been generated. The erase-safety tests are the ones
to care about — they are what keeps "loopback images only, dry-run by default"
a property of the code rather than of the documentation.

**Reproducing the benchmark** additionally needs **Node 18+** and **WSL
Ubuntu** with `testdisk foremost scalpel` installed (that's where the baseline
carvers live; they are Linux-only).

```bash
pip install -e ".[api,dev]"
npm install --prefix web
./run.sh
```

`run.sh` regenerates everything from scratch — disk images, decoder self-test,
our carve, all four baselines, and the comparison table. Nothing is cached, so
if it passes, the results are real.

Then, in two terminals:

```bash
python -m uvicorn api.main:app --port 8781
```

```bash
npm run dev --prefix web
```

and open **http://localhost:5183**.

Ports are deliberately non-default. 5173 and 8000 are what every other Vite and
FastAPI project on a dev machine grabs, and colliding with an unrelated
project's dev server is a confusing way to lose an evening.

**Git.** Initialised 7 Sept 2026, default branch `main`. Everything under
`corpus/` is gitignored — it is 4.9 GB and fully reproducible (synthetic images
from `tessera-gen-corpus`, CFReDS and govdocs1 by download). `.gitattributes`
pins `*.sh` to LF so the shell scripts keep working under WSL on a Windows
checkout.

---

## 10. What each folder is for

| path | one line |
|---|---|
| `GUIDE.md` | **New teammate? Start there.** How to run it, what it does, what breaks, glossary |
| `corpus/generate/synthetic.py` | Builds the test disk images and writes the ground-truth manifests |
| `corpus/images/` | Generated `.img` files and manifests — gitignored, fully reproducible |
| `carve/blocks.py` | Cluster-addressed view of a disk image; entropy pre-filter; header scan |
| `carve/validators/jpeg.py` | Resumable JPEG entropy-stream decoder — the hard constraint |
| `carve/priors/base.py` | Allocation priors: `LocalityPrior` and `UniformPrior` (ablation control) |
| `carve/beam.py` | Constrained beam search over cluster sequences — the core algorithm |
| `carve/carver.py` | Top-level entry point and CLI; finds headers, carves each file |
| `tests/` | The pytest suite. `pytest -m "not slow"` for the fast pass; plain `pytest` adds the end-to-end carves |
| `bench/test_decoder.py` | Decoder self-test as a READABLE REPORT — prints the DC separation numbers. The assertions inside it are also in `tests/test_jpeg_decoder.py`, which is what CI runs |
| `bench/run_baselines.py` | Runs the real PhotoRec / Foremost / Scalpel binaries via WSL |
| `bench/score.py` | Ground-truth scoring — **the only place SHA-256 comparison is allowed** |
| `bench/report.py` | Builds the comparison table and `report.json` that the UI reads |
| `bench/diagnose_path.py` | Debug tool: walks the true cluster path and shows why the search chose otherwise |
| `api/main.py` | FastAPI backend; streams the live beam-search trace over SSE |
| `web/` | React + Vite UI — disk map, live fragment graph, side-by-side, benchmark table |
| `model/pairs.py` | Adjacency pair mining; same-file negatives, split by file |
| `model/auc_experiment.py` | The AUC experiment: bi-encoder, seam cross-encoder, histogram control |
| `erase/ntfs.py` | Minimal read-only NTFS/$MFT parser (no pytsk3 dependency) |
| `erase/residue.py` | Finds deleted content surviving in unallocated MFT records |
| `erase/wipe_residue.py` | Erases it. Dry-run default, refuses block devices |
| `erase/validate.py` | SP 800-88r2 4.5.2 validation — a verdict that can REJECT |
| `erase/certificate.py` | Appendix C certificate, Ed25519, hash-chained |
| `erase/demo_residue.py` | The end-to-end residue demonstration |
| `erase/drive.py` | Drive-level sanitisation: technique selection, certificate, adversarial re-scan |
| `erase/metadata.py` | Metadata scrubbing |
| `corpus/generate/real_photos.py` | Builds disk images from REAL photographs, with other real photos as decoy filler |
| `bench/success_rate.py` | Byte-exact success rate over randomised out-of-order layouts |
| `LICENSE` | MIT |

---

## 11. Working agreements for AI sessions

- Phase 0 results in section 2 are **measured, not estimated**. If you change
  the carver, re-run `./run.sh` and update those tables — do not leave stale
  numbers in this file.
- When a design decision comes from a failing test, write the reason in a
  comment at the code, and add it to section 6 here. That is how this project
  keeps its memory.
- Do not soften the honest-limitations language anywhere in the repo. The
  benchmark table beating us on one file (PhotoRec on `easy.img`) stays in.
