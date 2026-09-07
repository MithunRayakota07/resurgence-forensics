# Roadmap

**Goal:** a finished, public, deployable project that stands up as portfolio
work for a software engineering internship.

**Not the goal any more:** completing SIH26149. The hackathon framing is being
removed. Nobody reading a resume knows what that problem statement said, and
measuring the project against it now only makes it look unfinished.

---

## What this is, stated so it survives scrutiny

A file carver that reassembles **out-of-order** fragments from raw disk
images, plus a secure-erasure side that finds and destroys deleted-file
residue and issues a signed certificate for it.

The claim to lead with: *out-of-order fragment reassembly*. Not "beats
commercial forensic tools" — that is true on our corpus and false on NIST's,
and anyone serious will find out within an hour.

---

## Where it stands

**Working and verified end to end** (full run, 8 Sept 2026):

| | result |
|---|---|
| `easy.img` (2 files, in order) | 2/2 byte-exact, 91.9 s |
| `hard.img` (3 fragments, backward jump) | 1/1 byte-exact, 32.5 s |
| best baseline (PhotoRec + brute force) | 1/2 easy, 0/1 hard |

No other tool recovers `hard.img` at any speed.

**Also done:** installable package, 73 tests, CI across Linux and Windows,
safety interlocks that are tested rather than merely documented.

**Known limitations, kept in writing on purpose:**

- **0/6 on the NIST CFReDS benchmark.** The true paths are reachable; the
  search picks wrong ones. The cause is measured and fundamental, not a bug.
- **Slowest tool that works.** Runtime at scale is unmeasured.
- **No learned model ships.** `model/` is a study; the carver uses none.
- **Confidence is uncalibrated** and labelled as such everywhere.
- **Erasure never touches physical hardware.** Device commands are modelled
  and recorded as planned, never issued. Loopback images only.

---

## Phase A — get it published

Everything here is presentation, not engineering. It is also what decides
whether anyone ever reads the engineering.

| # | step | why it matters |
|---|---|---|
| A1 | **Rename to Tessera** — DONE, 49 references across 15 files | A tessera is one tile of a mosaic; reassembling scattered tesserae is literally what the carver does. The old name read wrong and collided with a well-known LLM. |
| A2 | **Rewrite the README** — DONE | Hero screenshot, honest limits stated up front (the NIST 0/6 was previously absent from the README entirely), measured success rate. |
| A3 | **Add MIT LICENSE** — DONE | A public repo with no licence legally means nobody may use it. |
| A4 | **Strip SIH artifacts** — DONE | Competition brief, team rules, key dates, the 55%-effort cap and `docs/slide-wording.md` are gone. One paragraph of history stays, explaining why there are three capabilities. |
| A5 | **Publish, confirm CI green, add the badge** | The badge needs the repo URL, so it comes last. **This is the only step left.** |

Done along the way: real-photograph corpus builder, a measured success rate
(54/54 randomised out-of-order layouts), and the stale claims that `erase/` and
`model/` were empty.

---

## Phase B — make the claims stronger

Optional, and each is independently useful. Do them after publishing, in this
order.

0. **Find out why the known failing layout fails.** A reproducible failure is
   checked into `tests/test_real_photo_corpus.py`, and three controlled
   experiments — file size crossed with decoy density, image content, and image
   size — all failed to explain it. 54 of 54 randomised layouts pass and this
   one does not. Whatever separates them is the most interesting unanswered
   question in the project.

1. **Measure runtime on a 1 GB image.** A naive extrapolation gives ~100 days
   for a 2 TB drive. That is now known to be wrong — per-file search cost does
   not depend on image size at all (identical candidate counts on 8 MiB and
   16 MiB). What still scales is the header scan and the file count, and
   neither has been measured.

2. **Error bars on the AUC study.** Currently a single seed. Repeat across
   3–5 and probe whether the DOC/XLS signal is content adjacency or just OLE
   container structure.

3. **The restart-marker experiment.** This is the one that matters
   scientifically. Section 5f infers that restart markers explain why
   rejection ranges from 100% down to 19%, but it is inferred from a perfect
   correlation across six files where only two have markers. Re-encode the
   same images with and without restart intervals, hold everything else
   fixed, and measure. If it holds, a hunch becomes a result with a mechanism.

---

## Phase C — larger bets

Weeks, not days. Only worth starting if the project keeps your interest after
Phase A.

- **Extend the carver to DOC/XLS** so the learned adjacency actually ships
  instead of sitting in `model/` as a study. This is the honest fix for the
  best result in the repo not being in the product.
- **Calibrate the confidence score** so it becomes a probability rather than a
  number with an asterisk.

---

## Explicitly out of scope

- Issuing real ATA/NVMe sanitize commands to physical hardware. The safety
  boundary is deliberate and should stay.
- Rescuing the JPEG story with machine learning. Two independent measurements
  say it does not work on entropy-coded data. Do not spend time here.
- Any deadline inherited from the hackathon.
