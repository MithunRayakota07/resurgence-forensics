# Module 2 novelty audit — is it real, or did we beat a strawman?

Verdict up front: **partially strawman.** Our headline number (359/360 files
recovered, 99.7%) was measured against a baseline weaker than a free tool that
has existed since ~2000. The content-recovery claim collapses against a correct
baseline. A narrower claim survives, and it is genuinely under-served — but it
is not the claim we were making, and it is smaller.

---

## 1. What real products actually do about residue (primary sources)

**SDelete (Microsoft Sysinternals)** — its own documentation, verbatim:

> "*SDelete* must also fill any existing free portions of the NTFS MFT (Master
> File Table) with files that fit within an MFT record ... When *SDelete* can
> no longer even create a new file, it knows that all the previously free
> records in the MFT have been completely filled with securely overwritten
> files."

So **SDelete has handled MFT-resident residue for ~25 years.** It also states
the gap it does *not* close:

> "*SDelete* securely deletes file data, but not file names located in free
> disk space ... deleting them would require direct manipulation of directory
> structures ... *SDelete* has no way of allocating this free space so that it
> can securely overwrite it."

**Active@ KillDisk** — documentation:

> "Wipe unused space in MFT/Root area ... Unused space in MFT records may
> contain residual confidential data (file names, file attributes, resident
> file data) from the files that previously occupied these spaces."

KillDisk explicitly targets resident file data, file names, and attributes in
unused MFT space. It is a paid product but the capability is documented.

**cipher /w (Windows built-in)** — three-pass free-space overwrite, but native
tools "only wipe unallocated sector space, whereas true MFT wiping requires
low-level raw disk access." So cipher does **not** reach MFT-resident data.
This is the one mainstream tool with the same gap our naive baseline had.

**BleachBit** — open GitHub issue #1436: "wipe free space doesn't wipe/hide
file names". Older versions attempted MFT filling and *removed* it "because of
side effects". So BleachBit currently does not close either gap, and tried.

**Eraser (Heidi)** — "cleans the MFT by only erasing entries which already are
deleted ... as part of doing an unused space erase." Claims MFT erasure.

**Blancco / BitRaser** — enterprise file erasers advertising free-disk-space
and slack wiping. Exact MFT/directory coverage is behind support paywalls and
I did not confirm it from primary sources. Assume the leaders are at least as
capable as SDelete; do not claim otherwise without evidence.

**Summary table:**

| residue path | SDelete | KillDisk | cipher /w | BleachBit | **us** |
|---|---|---|---|---|---|
| MFT-resident content | **yes** | **yes** | no | no | yes |
| $FILE_NAME in MFT record | partial | yes | no | no | yes |
| **$I30 directory-index filenames** | **no (documented)** | "root records" (unclear) | no | no | **no** |
| $LogFile / $UsnJrnl | no | no | no | no | no |
| Volume Shadow Copies | no | no | no | no | no |
| SQLite WAL | no | no | no | no | no |

---

## 2. What our 99.7% was actually measured against — the strawman

Our baseline `v3_freespace_wiped.img` was built by **our own shell script**:
fill free clusters with `dd if=/dev/zero`, then delete the fill file. That is
what cipher /w does. It is **not** what SDelete does, because it skips the
step SDelete documents: filling free MFT records with tiny files.

I then built an **SDelete-equivalent baseline** (`sdelete_equivalent.sh`):
overwrite free clusters, then create tiny files until creation fails, exactly
as SDelete's documentation describes. Measured on the identical scenario:

| baseline | recoverable file CONTENT | 
|---|---|
| our naive `dd` fill (what we quoted) | **359 / 360** |
| SDelete-equivalent (fills MFT records) | **0 / 360** |

**Our content-recovery claim collapses against a correct baseline.** Filling
free MFT records destroys exactly the resident content we were recovering. The
99.7% number is real arithmetic against a baseline that no serious tool uses.

**This must not go on a slide as stated.** A judge who knows SDelete refutes it
in one sentence.

---

## 3. What actually survives — the narrower, real claim

After the SDelete-equivalent wipe, the original case **filenames** still appear
747 times in the image — 705 of them in the **$I30 directory index**, a B-tree
stored in the parent directory's own clusters. SDelete documents that it cannot
reach this. cipher cannot. Our tool also cannot (we target $FILE_NAME in MFT
records, not the $I30 index).

So the honest state of the art:

* **Resident content** — solved by SDelete/KillDisk since ~2000. Not our
  contribution.
* **$I30 directory-index filenames** — SDelete explicitly cannot; cipher and
  BleachBit cannot; KillDisk's "root records" wording is ambiguous and
  unverified. **Nobody in the open/free tier demonstrably removes these, and
  neither do we yet.** This is the genuine gap — but it is a gap we have
  *measured and named*, not one we *close*.

The defensible module-2 claim is therefore:

> "Not a new erasure capability — SDelete has filled free MFT records for
> twenty years. Our contribution is the **validation** layer: a tool that runs
> its own recovery engine against its own output, enumerates the residue paths
> it did and did not check, and **refuses to certify APPROVE when coverage is
> incomplete**. On our own erase it returns ESCALATE, because the $I30 index
> and nine other paths went unexamined. No shipping tool we surveyed does
> this — they report 'overwrite complete', which is NIST verification, not
> NIST validation."

That claim is true, it maps to SP 800-88r2 §4.5.2, and it survives the SDelete
objection because it is not an erasure-coverage claim at all.

---

## 4. Why "residue-aware erasure" was not solved in a Sunday evening — real reasons

It **was** solved, for the parts that matter to a wiping tool. The reasons the
*remaining* gaps persist:

1. **The main gap (resident content) was already closed.** SDelete/KillDisk did
   it. There was nothing to solve; we just did not check the incumbent first.
2. **The $I30 gap is genuinely hard and low-value.** Directory index space is
   not allocatable to files, so it cannot be reached by the create-files trick
   every free tool relies on. Closing it requires parsing and rewriting NTFS
   B-tree index structures on a live volume — risky, filesystem-version
   specific, and it only leaks *filenames*, not content. Low reward, real risk.
3. **Enterprise buyers wipe whole drives.** Blancco/KillDisk's core market is
   decommissioning, where the answer is "sanitize the entire device" and
   selective-file residue is moot. Selective erasure that chases every metadata
   path is a niche within a niche.
4. **Standards did not require it.** Validation (as opposed to verification)
   is new in SP 800-88r2 (Sept 2025). Before r2, "overwrite completed" was a
   defensible answer. The *validation* framing is genuinely recent — which is
   the one place our timing is on our side.

None of these are flattering to a "we discovered an unsolved problem" story.
They are why the honest claim is the validation layer, not the erasure.

---

## 5. Academic literature — this is well-trodden

* Resident-file survival in the MFT after deletion is textbook NTFS forensics
  (Carrier, *File System Forensic Analysis*, 2005).
* "A Method of Traceless File Deletion for NTFS File System" (Springer, 2022)
  and "The Research of Fast File Destruction Based on NTFS" (Springer, 2012)
  both address destroying NTFS metadata traces including MFT records.
* MFT-slack filename/timestamp residue is standard DFIR, with mature tools
  (Zimmerman's MFTECmd, Suhanov's dfir_ntfs) that recover exactly what our
  scanner recovers.
* $SI/$FN timestamp divergence as a timestomping indicator is well established
  (SANS DFIR curriculum, Zimmerman). Our metadata detector re-implements a
  known technique correctly; it does not invent one.

**Nothing in module 2's recovery or detection is a new observation.** We should
cite Carrier and the timestomping literature, not present these as findings.

---

## 6. Attacking our implementation — what breaks on a real volume

We tested on a `mkntfs -F` volume: 256 MiB, 400 tiny files, no OS running. A
real Windows system volume differs in ways that break our code:

1. **Non-resident $MFT.** Our `ntfs.py` stops at the first non-`FILE` record
   and does not parse $MFT's own run list. On a real volume the $MFT is
   fragmented across the disk; we would read only its first fragment and miss
   most records. **This is a hard limitation, not a tuning issue.**
2. **$DATA in an ATTRIBUTE_LIST.** Large or heavily-attributed files split
   attributes across multiple records via $ATTRIBUTE_LIST (type 0x20). We do
   not follow it, so we would misread those files.
3. **Compression / sparse / encrypted resident data.** SDelete devotes most of
   its logic to NTFS-compressed and sparse files. We handle none of it; a
   compressed resident attribute would be read as raw compressed bytes.
4. **Live locks.** A running OS holds $MFT open; we cannot get raw write access
   to a mounted system volume at all. Our tool only works on an unmounted
   image — which is the correct safe scope, but means it is an offline
   forensic tool, not a live sanitizer.
5. **Alternate Data Streams, hard links.** Multiple $FILE_NAME and $DATA
   attributes per record; we read the first of each and would miss the rest.
6. **Update Sequence Number robustness.** Our fixup handling assumes clean
   records; a partially-overwritten record can fail fixup and we skip it
   silently, undercounting residue.

Every one of these means the 359/360 (or the corrected 0/360) number is
specific to a pristine synthetic volume and would not reproduce on a real disk
image without substantial more work.

---

## Bottom line for the pitch

* **Cut** "we recover data that secure-delete tools miss" as a content claim.
  SDelete refutes it. Keep it only for the **$I30 filename** path, and only if
  we actually close it (we have not).
* **Keep and lead with** the **validation** claim — the tool that refuses to
  APPROVE its own incomplete work. That is real, recent (r2), and unmatched by
  the tools we surveyed.
* **Cite** Carrier, the Springer traceless-deletion papers, and the
  timestomping literature. Do not re-announce known forensics.
* The module is **partially novel**: the validation framing is novel; the
  recovery, erasure, and metadata detection are competent re-implementations of
  known techniques.
