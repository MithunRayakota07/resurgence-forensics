# Approved slide wording

Every line here is checked against a measurement. If a line is not in this
file, it has not been approved and should not go on a slide.

**The rule:** never state the learned claim unqualified. It is split by format,
and the split was decided on 23 Aug after measuring both sides.

---

## Slide 1 — what this is

> Recover files that should not be gone. Destroy files that should be.
> Prove both.

SIH26149 (NTRO). Three modules: drive sanitisation with audit trails,
selective erasure with metadata scrubbing, forensic carving with confidence
scoring.

---

## Slide 2 — the gap

> On the US government's own forensic test images, **Magnet AXIOM (2024),
> FTK 8.0, Autopsy 4.21, R-Studio (2024) and Forensic Explorer (2025) all
> carve fragmented JPEGs with the filler still inside the file.** NIST
> documents it. X-Ways documents its own limitation. Not one reassembles.

Sources: DHS/NIST CFTT test reports, per tool, cited in `CLAUDE.md` §4.

---

## Slide 3 — the contribution, split by format

**Say it this way. Do not merge the two rows.**

| data | what does the work | measured |
|---|---|---|
| **Compressed** (JPEG, GIF) | format validator + constrained search | learned adjacency **0.563** on JPEG entropy-stream — below a byte-histogram baseline |
| **Documents, spreadsheets** | **learned adjacency over raw binary** | **0.838** XLS, **0.799** DOC vs near-negatives, both beating the baseline |

> "We measured where a learned model helps and where it doesn't. On compressed
> images it doesn't — 0.563, barely above chance and worse than a byte
> histogram — so we don't claim it. There, the contribution is the format
> validator and the search. On documents and spreadsheets it does work, and
> that's where the learned claim stands."

**Never say:** "we learn the edge-weight function" without naming the format.
For JPEG it is false and we have the number that disproves it.

**Caveats that travel with 0.838 / 0.799, always:** single seed; DOC and XLS
are OLE compound files, so part of the signal is probably container structure
rather than content adjacency.

---

## Slide 4 — prior art, stated narrowly

> Sequencing is not ours — Pal & Memon formulated it in 2006.
> Out-of-order carving is not ours — Huijsmans, Kuijsten, Jonker & van Beek,
> LNCS 16365, 2026. We target their stated open problem: efficiency in
> practical settings.
> Allocation priors are not wholly ours — Karresand et al. 2019-2020 use
> allocation behaviour to prioritise **where** to search; we use it as a
> fitted pairwise gap likelihood **inside** the sequencing objective, ablated.

---

## Slide 5 — our results, including the losses

> `hard.img`: 1/1 byte-exact. PhotoRec with brute force: nothing.
> `easy.img`: 2/2. PhotoRec with brute force: 1/2, and 19x slower.
> **NIST CFReDS: 0/6.**

> "We scored zero on the government corpus. We can show all six correct
> answers are reachable — feed our validator the true fragment order and it
> decodes every file completely. The failure is our search, and we measured
> why: on that corpus the filler is photometrically indistinguishable from
> real image data, 0.890 against 0.917."

**Keep the PhotoRec row that beats us.** A table where you win everything is
not believable.

---

## Slide 6 — erasure, and the line to lead with

> A conventional secure-delete workflow: delete the files, then wipe free
> space. We then recovered **359 of 360 deleted files intact** — because NTFS
> stores small files inside the MFT record, where cluster-level wiping never
> looks. Our tool erases them. Then we look again and find nothing.

**Then the line that matters most:**

> "And our certificate does not say APPROVE. It says **ESCALATE**, because we
> only examined one residue path out of ten. The tool refuses to approve its
> own work."

That is NIST SP 800-88r2 §4.5.2 validation — a decision that can reject —
which no shipping tool implements.

---

## Slide 7 — the questions you will be asked

**"Isn't this just PhotoRec but better?"**
> No. PhotoRec's search only extends forwards from the header. When a fragment
> sits earlier on the disk it returns nothing. Ours searches both directions.
> Structural difference, not a tuning difference.

**"Everything's an SSD, TRIM wipes it."**
> TRIM fires on deletion. Our problem statement is *formatted or corrupted*
> media — lost partition table, damaged filesystem, ransomware — where nothing
> was deleted, so TRIM never runs. It also doesn't reach SD cards, camera
> media or most USB enclosures.
> **Do not say** "TRIM never fires on a reformat" — false since Windows 8.
> **Do not claim** phones.

**"You made these test images yourself."**
> Yes, and that's the softest part of our evidence, which is why we also ran
> NIST's corpus and scored 0/6. Here is why, and here is the proof the answers
> are reachable.

**"Does it use AI to reconstruct the missing parts?"**
> No. It never invents data. Every byte comes off the disk. If it can't
> complete the file exactly, it reports failure rather than a guess.

**"What's the strongest tool for this?"**
> Adroit Photo Forensics — the commercialisation of the research we build on.
> We tried to obtain it; the vendor no longer distributes it. So that
> capability is not commercially available today. **Do not claim we
> benchmarked against it.**

---

## Words that are banned

* "military-grade" — means nothing
* "DoD 7-pass" — obsolete; SP 800-88r2 §3.1.1 says multi-pass is not needed
* "blockchain" — one issuer, so it adds nothing; we use a signed hash chain
* "99% accurate" — our confidence score is **uncalibrated** and labelled so
* "we learn the edge weights" — not for JPEG, ever
* "best approach" / "most powerful" — unsupported superlatives
