# Modules 1 & 2 — Secure Erasure, design

SIH26149 requires three modules. We have built part of module 3 (carving). This
document specifies modules 1 and 2 and, more importantly, says which parts are
ordinary engineering and which parts are actually novel — so nobody on the team
oversells the easy half.

Everything normative below is taken from primary sources, read directly:
**NIST SP 800-88r2** (September 2025, 48 pp.) and the standards it defers to.

---

## 0. What the standard actually requires in 2026

Most teams will cite SP 800-88 Rev 1 from a blog post and implement a 3-pass
DoD wipe. Every part of that is now wrong. From r2's own change log and body:

**0.1 — Rev 1 is withdrawn.** r2 was published 26 Sept 2025 and superseded it
the same day.

**0.2 — Multi-pass overwriting is explicitly obsolete.**
r2: *"The 'clear' method was clarified such that multi-pass overwrite is not
needed. This counters the obsolete DoD 5220.22-M language that mandates a
certain number of overwrite passes and patterns."*
It further notes DoD removed overwriting specifications from NISPOM in **2006**.
Anyone still shipping "DoD 7-pass" is implementing a standard abandoned twenty
years ago. **We implement single-pass and say why.**

**0.3 — r2 no longer specifies techniques. It defers to IEEE 2883.**
r2: *"Apart from CE … all sanitization technique and tool details have been
replaced with recommendations to comply with IEEE 2883, NSA specifications, or
an organizationally approved standard."*
IEEE 2883-2022 is now the technical authority, and it carries the actual
interface commands (SATA, SAS, NVMe) that did not exist when Rev 1 was written.
**Cite both.**

**0.4 — Verification and validation are different things, and validation is new.**

* **Verification** (§4.5.1) — did the technique complete? Check exit status,
  errors, anomalies, device health. r2 explicitly says elaborate sampling of
  contents is *not* required.
* **Validation** (§4.5.2) — was the target data *effectively* sanitized? This
  is a **decision that can REJECT**, forcing a different technique or escalation
  to a stronger method.

r2 lists what invalidates a sanitization, and one bullet is our entire pitch:

> *"The scope of the sanitization (i.e., target data) was too narrowly focused.
> For example, an ISM that employs overprovisioning is sanitized using clear
> sanitization technique based on simple writes to overwrite existing contents
> and potentially leaves a substantial amount of user data unchanged."*

Others: media inaccessible through the interface due to errors; technique
inappropriate for the media (**it names degaussing an SSD** as completing
successfully while sanitizing nothing); unqualified personnel or uncalibrated
tools; outcome below the organisation's minimum.

**0.5 — Degaussing is not "destroy"** even when it bricks the device.

**0.6 — Crypto-erase has hard preconditions.** Key sanitization must be
zeroization per **ISO/IEC 19790**; **ISO/IEC 27040** requires that *all copies*
of the target keys can be sanitized. Acceptable key types are enumerated
(data-encryption, key-wrapping, master/derivation, private key-transport).

**0.7 — Certificate contents are specified** (§4.6, Appendix C). Minimum:
manufacturer, model, serial, property number, media type, media source,
pre-sanitization categorization (optional), **method** (clear/purge/destroy),
**technique** (overwrite/block erase/CE/degauss), **tool including version**,
**verification method**, **validation**, and for each person: name, title, date,
location, contact, signature.

---

## 1. Module 1 — Drive-level sanitisation

### 1.1 What is table stakes

Detect the device and its interface; select and issue the right sanitize
operation; report completion. Specifically:

| media | technique | interface command |
|---|---|---|
| SATA SSD / HDD | Purge | ATA `SANITIZE` (`BLOCK ERASE` / `OVERWRITE` / `CRYPTO SCRAMBLE`) |
| NVMe SSD | Purge | NVMe `Sanitize`, or `Format NVM` with secure erase settings |
| SAS | Purge | SCSI `SANITIZE` |
| any, encrypted since provisioning | Purge | Cryptographic Erase |
| HDD, logical only | Clear | single-pass overwrite of all addressable space |

Do not claim novelty here. Every commercial tool does this.

### 1.2 What is actually novel — **validation as a decision, not a checkbox**

No tool we surveyed implements §4.5.2. They all implement §4.5.1 and call it
verification: *"overwrite completed, 0 errors."* NIST now asks a harder
question that can come back **REJECTED**.

We implement a **graded verdict** — `APPROVE` / `REJECT` / `ESCALATE` — with
reasons, computed from evidence we can actually gather:

| check | evidence source | why it matters |
|---|---|---|
| Is the media flash-based? | rotation rate, device ID | overwrite ≠ sanitize on flash |
| Over-provisioning present? | capacity vs NAND, `nvme id-ctrl` | r2's named failure case |
| HPA / DCO present? | `hdparm -N`, ATA `READ NATIVE MAX` | hidden regions outside "all addressable" |
| Remapped / pending sectors | SMART 5, 197, 198 | data stranded in retired blocks |
| Technique appropriate for media? | rule table | r2: degauss-an-SSD completes but sanitizes nothing |
| Was the device encrypted from provisioning? | TCG Opal / eDrive status | CE precondition; if not, CE is invalid |
| Are all key copies sanitizable? | key store enumeration | ISO/IEC 27040 requirement |
| Known-flawed SED implementation? | model lookup | see 1.3 |
| Confidentiality horizon vs technique | operator input | 25-year secrets ≠ 1-year secrets |

The output is a **verdict with reasons**, not a green tick:

> `ESCALATE` — Cryptographic erase completed. Drive model has documented
> encryption implementation flaws (Meijer & van Gastel, 2018). Stated
> confidentiality horizon 30 years. Recommend overwrite followed by physical
> destruction.

**A tool that knows when it should not be trusted is the differentiator.**

### 1.3 Distrusting crypto-erase, with a citation

Crypto-erase is mathematically sound and operationally fragile. Meijer & van
Gastel, *"Self-encrypting deception: weaknesses in the encryption of solid
state drives"* (Radboud University, 2018) reverse-engineered SSD firmware from
three vendors and found, across **three Crucial and four Samsung models**, that
encryption could be bypassed entirely — password and data-encryption key not
cryptographically linked, single DEK for the whole disk, insufficient DEK
entropy, JTAG and vendor diagnostic access, unsigned code execution. It also
affected BitLocker where it deferred to hardware encryption.

So: ship a **model → known-flaw table**, and when a drive matches, the verdict
downgrades and says why. This is a small database and a large amount of
credibility.

### 1.4 The certificate

Implement Appendix C exactly — every field in 0.7. Then add what NIST does not
require but an investigator wants:

* the **validation verdict and its reasons**, not just "completed"
* engine version, corpus version, ruleset version
* **Ed25519 signature** over a canonical serialisation
* an append-only **hash chain** linking each certificate to the previous one

**No blockchain.** Blockchain solves agreement between mutually distrusting
parties. A sanitization certificate has one issuer. A signed hash chain gives
tamper-evidence and ordering without the theatre — and a previous SIH team
already pitched blockchain wipe certificates, so it reads as decoration.
Have this rationale ready as a slide; being asked "why not blockchain?" is a
gift if you can answer it in one sentence.

---

## 2. Module 2 — Selective erasure and metadata scrubbing

This is where the interesting failure modes live.

### 2.1 The problem in one line

**Overwriting a file's clusters does not erase the file.** Copies and traces of
its content, name, and metadata exist in a dozen other places, and every
mainstream tool leaves most of them behind.

### 2.2 The residue map — NTFS

| location | what survives | why cluster-overwrite misses it |
|---|---|---|
| **`$MFT` resident data** | **the entire file content** | Files under ~600 B live *inside* the MFT record. Deleting frees **no cluster**. Content survives byte-for-byte until the record is reused. |
| `$MFT` slack | old attributes, names, timestamps | record not zeroed on delete |
| `$LogFile` | recent metadata transactions | circular journal, never targeted |
| `$UsnJrnl` | change history, filenames | separate stream |
| `$I30` directory indexes | deleted filenames in index slack | index nodes not compacted |
| **Volume Shadow Copies** | **whole prior versions of the file** | separate block store |
| File slack | tail of previous file in last cluster | outside the new file's length |
| Alternate Data Streams | hidden payloads | not enumerated by naive tools |
| `thumbcache_*.db` | **thumbnails of deleted images** | separate cache DB |
| Windows Search index | indexed *content* and filename | separate DB |
| Prefetch / Jump Lists / Recent / ShellBags | filenames, access history, paths | registry + separate files |
| `pagefile.sys`, `hiberfil.sys`, `swapfile.sys` | file content that transited RAM | separate files, often skipped |
| SQLite `-wal` / `-journal` | browser history, chat messages | sidecar files not deleted with the DB |
| SSD over-provisioning / remapped blocks | prior contents of retired cells | not addressable via host interface |
| HPA / DCO | anything hidden there | outside "all addressable locations" |

**ext4 equivalents:** journal (`jbd2`), inline data in inodes (same class of
problem as MFT-resident), orphan inode lists, directory entry slack, `lost+found`.

### 2.3 The evidence that this gap is real

Microsoft's own SDelete documentation states it **"securely deletes file data,
but not file names located in free disk space."** SDelete also implements
**DoD 5220.22-M** — the standard NIST r2 explicitly calls obsolete.

That is the single most widely used Windows secure-delete tool, admitting a gap,
while implementing a withdrawn standard. Quote it directly; it is not a claim we
are making about a competitor, it is their own documentation.

### 2.4 Metadata scrubbing done properly

**NTFS stores eight timestamps per file, not four** — four in
`$STANDARD_INFORMATION` and four in `$FILE_NAME`. Tools that touch only the
first set leave a detectable inconsistency that is itself a forensic artefact
(and a well-known timestomping tell). Handle both, and say so.

Also: EXIF/XMP/IPTC in images (including GPS and device serial), Office document
properties and revision history, PDF metadata and incremental-update history,
ADS, and `$FILE_NAME` entries in parent directory indexes.

### 2.5 What is novel here

Not "we overwrite a file." The novel claims are:

1. **Residue-aware scope.** We enumerate and target the locations in 2.2 rather
   than only the file's clusters. That is a documented gap in the market leader.
2. **Erasure verified by an independent adversary** — see §3.
3. **Both timestamp sets**, so scrubbing does not leave its own signature.

---

## 3. The integration — why this is one tool, not three

This is the part that turns three bolted-together modules into a single system,
and it is the strongest thing in the whole project.

### 3.1 The naive version, which is worthless

*"We wipe the drive, then attack it with our own recovery engine, and it
recovers nothing."*

After a correct overwrite there are zero bits left. **Any** recovery engine
finds nothing. `cat /dev/zero` passes this test. A knowledgeable judge will say
so, and the claim collapses.

### 3.2 The version that is worth something

Point the carver at **the residue paths, not the overwritten region.**

> **Demo:** a competitor tool "securely deletes" an evidence file.
> Our carver then recovers it **in full** — from the `$MFT` resident record, or
> a Volume Shadow Copy, or the thumbnail cache.
> Our tool erases the same file, residue included.
> Our carver runs again and finds nothing.
> The certificate enumerates **every residue path checked**.

The claim is no longer *"we overwrote it three times."* It is:

> **"We attacked this with the best recovery tool we have, across every place we
> know data hides, and it failed."**

That is a sanitization **validation** in NIST's exact sense — an independent
decision that the target data was effectively sanitized — and it is what
§4.5.2 asks for and no shipping tool provides.

### 3.3 Why module 3's failures do not hurt here

Our carver scored 0/6 on NIST's fragmented images because reassembling
scattered *fragments* is hard. Residue recovery is a different and **easier**
task: `$MFT`-resident content is contiguous, shadow copies are whole files,
thumbnails are complete JPEGs. The carver is strong enough for this job today.

---

## 4. What we must NOT build

* **No multi-pass DoD wipes.** Obsolete per r2 §3.1.1. One pass, and explain why.
* **No blockchain.** §1.4.
* **No "military-grade" language.** It means nothing and signals inexperience.
* **No claim that overwriting sanitizes flash.** It does not, and r2 names it.
* **No verification-only reporting.** Verification is the old standard; the new
  requirement is validation.

---

## 5. Safety — non-negotiable

This module destroys data irreversibly. Before any destructive path:

1. **Loopback images only** during all development. Never a physical device.
2. **Refuse the system disk** outright, no override.
3. **Refuse any mounted volume.**
4. Require the operator to **type the device serial number** to proceed.
5. **Dry-run by default**; destruction requires an explicit flag.
6. Log the intended target and full plan *before* the first write.

One mistake here ends the project and possibly someone's coursework.

---

## 6. Build plan

Ordered so that a new teammate can start immediately and nothing blocks on
research.

| # | task | difficulty | depends on |
|---|---|---|---|
| 1 | Device enumeration + media-type detection (safe, read-only) | low | — |
| 2 | Certificate data model + Ed25519 signing + hash chain | low | — |
| 3 | Validation rule engine (§1.2) with verdicts and reasons | medium | 1 |
| 4 | Loopback-image erase on a virtual disk, single pass | low | safety interlocks |
| 5 | Residue enumerator: `$MFT` resident, VSS, thumbcache, slack | **medium-high** | pytsk3 |
| 6 | Residue erasure | medium | 5 |
| 7 | Adversarial validation: point the carver at residue paths | medium | 5, module 3 |
| 8 | Known-flawed-SED model table | low | — |
| 9 | Metadata scrubber, both timestamp sets + EXIF | medium | — |
| 10 | Real hardware sanitize commands (ATA/NVMe) | high, risky | 1–4 done |

**Items 1, 2, 4, 8 are ideal first tasks for new teammates** — self-contained,
no research risk, immediately demonstrable.

**Item 5 is the technically interesting one** and is where the novelty is.

**Item 10 should be last and may stay out of scope for the finale.** Issuing
real sanitize commands to real hardware is where irreversible accidents happen,
and a loopback demonstration proves the same logic.

---

## 7. Sources

* NIST SP 800-88r2, *Guidelines for Media Sanitization*, September 2025 — read
  directly, 48 pp. Sections 3.1, 3.2, 4.5, 4.6, Appendix C, Appendix D.
* IEEE 2883-2022, *Standard for Sanitizing Storage* — the technique authority
  r2 defers to. **We have not read the full text (paywalled); claims about its
  internals should be checked before quoting.**
* ISO/IEC 19790 (key zeroization), ISO/IEC 27040 (all key copies) — cited by r2.
* Meijer & van Gastel, *Self-encrypting deception*, Radboud University, 2018.
* Microsoft Sysinternals SDelete documentation — the "file names in free space"
  limitation and DoD 5220.22-M implementation.
