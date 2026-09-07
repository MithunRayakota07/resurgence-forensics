# SUTRA — Fragment Reassembly Engine

**Phase 0 prototype.** Recovers fragmented JPEGs from raw disk images that
PhotoRec, Foremost and Scalpel cannot.

> Fragment sequencing has been formulated since 2006 (Pal & Memon), and
> out-of-order carving was shown feasible in 2026 (Huijsmans et al.), which
> left **efficiency in practical settings** as the open problem.

**The contribution is split by format, because we measured where each approach
works and where it does not.**

| data | what does the work | evidence |
|---|---|---|
| **Compressed** (JPEG, GIF) | format **validator + constrained search**. No learned model. | Learned adjacency measures **0.563 AUC** on JPEG entropy-stream data — near chance, and below a byte-histogram baseline. It does not work here and we do not claim it does. |
| **Documents, spreadsheets** (DOC, XLS) | **learned adjacency** over raw binary, which nobody has built | **0.838** XLS, **0.799** DOC against near-negatives, both beating the baseline, with the margin *widening* under realistic difficulty. |

Plus an allocation prior, ablatable, on both paths.

SIH26149 (NTRO) — *Integrated Secure Data Erasure and Advanced File Recovery Tool*.

---

## Results

Same disk images, same scoring harness, real installed binaries for every
baseline. Reproduce with `./run.sh`.

### `easy.img` — two JPEGs interleaved, fragments in order

| tool | byte-exact | time | EVIDENCE-A | EVIDENCE-B |
|---|---|---|---|---|
| **SUTRA** | **2 / 2** | **118.2 s** | **EXACT** | **EXACT** |
| PhotoRec + brute force | 1 / 2 | 257.9 s | EXACT | nothing recovered |
| PhotoRec (default) | 0 / 2 | 1.6 s | nothing recovered | nothing recovered |
| Foremost | 0 / 2 | 14.7 s | nothing recovered | corrupt, ssim 0.61 |
| Scalpel | 0 / 2 | 0.8 s | nothing recovered | corrupt, ssim 0.81 |

### `hard.img` — one JPEG, three fragments, **out of order with a backward jump**

| tool | byte-exact | time | EVIDENCE-C |
|---|---|---|---|
| **SUTRA** | **1 / 1** | **67.5 s** | **EXACT** |
| PhotoRec + brute force | 0 / 1 | 1.6 s | nothing recovered |
| PhotoRec (default) | 0 / 1 | 1.4 s | nothing recovered |
| Foremost | 0 / 1 | 0.9 s | corrupt, ssim 0.59 |
| Scalpel | 0 / 1 | 1.5 s | corrupt, ssim 0.59 |

*Re-measured 24 Aug on an idle laptop (i7-12700H), tools run sequentially. Runtime is thermally variable: hard.img ranged 49.9-75.7 s over five reps. Earlier published figures (7.6 s / 16.1 s) predate the byte-scaled search window and understated our runtime ~6x.*


**Read this honestly.** PhotoRec is run *with* `paranoid_bf` brute-force mode,
which exists specifically for fragmented JPEGs and uses libjpeg to find the
foreign block. On `easy.img` it beats the naive carvers and recovers one file
byte-exact — 19× slower than us, but exact. It fails on `hard.img` because its
search only extends **forwards** from the header, and there the next fragment
lies behind it.

Benchmarking against PhotoRec with that switch off would be rigging the
comparison, and anyone who knows the tool would catch it in one command.

---

## How it works

Carving runs at **4 KiB cluster** granularity, not 512 B sectors — real
filesystems allocate in clusters, so a 512 B unit multiplies the search graph
by 8× for no extra information.

**1. Format validator as a hard constraint.** `carve/validators/jpeg.py` is a
baseline JPEG entropy-stream decoder with resumable, snapshottable state. For
a candidate cluster it answers: *resuming Huffman decoding from exactly this
bit position, does the stream stay valid?* No IDCT — desync is the signal.
Candidates that desync, or that reach the full MCU count without landing on
EOI, are **eliminated**, not down-weighted.

**2. Photometric seam scoring.** Huffman validity alone is weak: two JPEGs
from the same encoder share the standard Huffman tables, so a foreign fragment
decodes as perfectly valid symbols for hundreds of MCUs. The discriminator is
the **Pearson correlation between the candidate's first MCU row and the row
directly above it**, which lies in the previous fragment. Measured: 0.69–0.92
within a fragment, well below 0.56 for foreign clusters.

**3. Beam search with run merging.** Inside a fragment the successor is the
next cluster along and the correlation says so loudly, so a path extends
without branching. Only where that evidence fails — a genuine fragment
boundary — does it open into the full candidate set.

**4. Allocation prior, ablatable.** `carve/priors/` — `LocalityPrior` (gap
distribution, contiguous-first) vs `UniformPrior` (the ablation control).
Run `--prior uniform` to get the without-prior number. On `hard.img`:

| prior | result | MCUs | candidates | time |
|---|---|---|---|---|
| `locality` | **byte-exact** | 1900 / 1900 | 8,041 | **73.7 s** |
| `uniform` (control) | failed to complete | 1868 / 1900 | 158,452 | **1753.9 s** |

Without the prior the search explodes: 19.7x the candidates, 23.8x the runtime,
and it still never closes the file. The prior buys both accuracy and ~24x the
speed. (An earlier version of this table said "roughly 2x"; that was measured
before the byte-scaled search window and understated the effect.)

## Findings that shaped the design

Each of these came from a failing test, and each is preserved as a comment at
the relevant place in the code:

- **Absolute |ΔDC| is the wrong metric and is actively harmful.** DC is
  differentially coded, so a foreign fragment inherits the predecessor's
  predictor as a constant offset and a flat, low-detail fragment scores a
  near-zero difference wherever it came from. Beam search found that exploit
  immediately and filled with flat garbage. Correlation is offset-free.
- **The seam carries the evidence, not the fragment.** Averaging DC continuity
  over a whole candidate mostly measures "is this internally smooth?", which
  is true of every photograph.
- **The allocation prior must not outrank the evidence.** At `W_PRIOR = 6.0`
  the contiguous bonus handed the next-cluster-along a ~35 point advantage and
  the search walked straight through a fragment boundary into filler. A prior
  that can overrule evidence is just encoding the answer you wanted.
- **MCU count alone is not a terminator.** Huffman self-synchronises, so a
  wrong chain decodes 1900 valid-looking MCUs and stops on the counter. The
  scan must actually *end* at EOI.
- **Natural image statistics are load-bearing.** With a procedurally-noisy
  test image the correct/foreign separation was 1.4×; with a photographically
  plausible one it is 3.4×.

## Honest limitations

- Confidence scores are **UNCALIBRATED**. "0.99" does not yet mean "99% of
  such files are byte-exact". Phase 2 replaces this with a fitted binary head
  plus split conformal.
- The allocation prior is **hand-set, not fitted** to real aged filesystems.
  `prior.is_measured` is `False` and the UI says so.
- Baseline sequential JPEG only. Progressive (SOF2) is rejected explicitly
  rather than silently mis-decoded.
- Test images are hand-laid, not produced by real filesystem aging. Phase 2
  builds the aging generator; random shuffling would flatter the prior.
- **No learned model yet.** Phase 0 is a hand-rolled validator and search.

## SHA-256 discipline

Comparison against the original file lives in `bench/score.py` and nowhere
else. In a real case there is no original, so a carver that consults one is
measuring nothing. The separation is mechanical, not a matter of discipline.

## Layout

```
corpus/generate/   disk image generator + ground-truth manifests
carve/validators/  format decoders used as hard constraints
carve/priors/      allocation priors (ablatable)
carve/beam.py      constrained beam search
bench/             scoring harness, real baseline runners, comparison report
api/               FastAPI, streams the live search trace
web/               React + Vite UI
model/ erase/      Phase 1 / Phase 3, empty
```

## Install

Needs Python 3.11+ and nothing else. The carver depends only on NumPy and
Pillow.

```bash
pip install -e .
```

That gives you the command-line tools:

```bash
sutra-carve corpus/images/hard.img --out recovered/
```

`sutra-erase-drive`, `sutra-erase-metadata` and `sutra-erase-residue` are the
erasure side; all three are dry-run by default and refuse anything that is not
a regular file. `sutra-gen-corpus` rebuilds the test disk images.

## Reproducing the benchmark

The comparison table additionally needs Node 18+ for the UI and WSL Ubuntu with
`testdisk foremost scalpel` — the baseline carvers are Linux-only.

```bash
pip install -e ".[api,dev]"
npm install --prefix web
./run.sh
```

Then `python -m uvicorn api.main:app --port 8781` and
`npm run dev --prefix web` → http://localhost:5183

Ports are deliberately non-default (8781 / 5183) to avoid colliding with other
projects' dev servers.

## Prior art — what is ours and what is not

Stated narrowly on purpose. Each item below has a published owner, and
overstating any of them is how this project gets dismantled in thirty seconds.

**Sequencing is not ours.** Shanmugasundaram & Memon (2003) and Pal & Memon
(2006) framed reassembly as a maximum-weight path / k-vertex-disjoint path
problem and solved it greedily (PUP). Learned adjacency plus shortest path also
exists for *image jigsaws* — Deepzzle (ECCV 2018), JigsawNet, DNN-Buddies.

**Out-of-order carving is not ours.** Huijsmans, Kuijsten, Jonker & van Beek,
*"How to Carve Out-of-Order Fragmented Files,"* LNCS 16365, Springer, 2026.
They also built a tunable fragmentation corpus generator. Their stated open
problem — *efficiency in practical settings* — is what we target.

**Allocation priors are not wholly ours.** Karresand, Dyrkolbotn & Axelsson
published three papers (2019–2020) on NTFS cluster allocation behaviour,
explicitly naming file carving as an application. Our narrower true claim:

> They use allocation behaviour to prioritise **where** to search. We use it as
> a **fitted pairwise gap likelihood inside the sequencing objective**, with an
> ablation showing what it's worth.

**What is ours, stated by format.** No open-source or published system learns
adjacency over raw binary disk fragments — verified against FiFTy
(classification only), FileScraper (Huffman table extraction), JPEG-Restorer
(syntactic + thumbnail affinity), JigsawNet and Deepzzle (pixels, not bytes).

But we only claim it where we measured it working:

* **Documents and spreadsheets — the learned claim stands.** XLS 0.838, DOC
  0.799 against near-negatives.
* **Compressed images — we make no learned claim.** JPEG entropy-stream 0.563,
  GIF 0.476. Below a byte-histogram baseline under both far- and
  near-negative regimes. For these formats the contribution is the validator
  and the constrained search, and nothing else.

Two caveats that travel with the document numbers, always: they are **single
seed**, and DOC/XLS are OLE compound files, so **some of what the model learns
is likely container structure rather than content adjacency**. Useful for
carving either way, but a narrower claim than "learned byte adjacency".

**Why `hard.img` is realistic, and not a contrived case:** van der Meer, Jonker
& van den Bos, *"A contemporary investigation of NTFS file fragmentation,"*
FSI:DI, 2021 — from 220 real Windows laptops, a significant portion of
fragmented files are stored **out of order**.

## Why this gap still exists in 2025

NIST/CFTT publishes per-tool results on a standard fragmented-carving
benchmark. On **fragmented JPG**, each of these carved files that *included the
fill between fragments* — the junk between fragments is left inside the output:

| tool | version | tested |
|---|---|---|
| Magnet AXIOM | 7.5.0.37231 | Jul 2024 |
| FTK | 8.0 | Jan 2024 |
| Autopsy | 4.21.0 | Feb 2024 |
| R-Studio T80+ | 9.3.191230 | Jul 2024 |
| Forensic Explorer | 5.6.8.4340 | Mar 2025 |

X-Ways documents its own limitation: carving "generally assumes contiguous file
clusters, so it produces corrupt files in case the files were originally stored
in a fragmented way." EnCase 7.09.05 scored 10 of 40 viewable-complete plus
9,054 false-positive BMPs.

Not one of them reassembles. Note also what the CFTT benchmark does *not*
cover: every layout is fragmented **in order** — out-of-order is untested by the
government benchmark entirely.
That is the gap, and Phase 1 is where we go after it.
