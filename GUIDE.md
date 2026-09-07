# Tessera — the guide

For a new teammate. Assumes you can code but have never touched digital
forensics. Every specialist word is explained the first time it appears, and
there's a glossary at the end.

If you only read one thing: **section 3** tells you what the program actually
does, as a story. That's the part you'll be asked to explain out loud.

---

## 1. Starting from a cold laptop

You need **Python 3.11+**, **Node 18+**, and **WSL** (Windows Subsystem for
Linux) with Ubuntu. WSL is needed because the three tools we benchmark against
— PhotoRec, Foremost and Scalpel — are Linux-only. We run the real binaries,
never reimplementations, because a reimplemented competitor is a straw man and
the whole value of our comparison table is that it's honest.

### 1.1 One-time setup

```bash
wsl --install -d Ubuntu --no-launch
```
Installs Ubuntu inside Windows. `--no-launch` skips the interactive
username/password setup — we only ever use it as root, non-interactively.
*Takes a few minutes. You should see "Distribution successfully installed."*

```bash
wsl -d Ubuntu -u root -- apt-get update -qq
```
```bash
wsl -d Ubuntu -u root -- apt-get install -y testdisk foremost scalpel
```
Installs the three baseline carvers. `testdisk` is the package that contains
PhotoRec. *You should end with three binaries; verify with:*

```bash
wsl -d Ubuntu -u root -- bash -c "which photorec foremost scalpel"
```
*Expect three paths under `/usr/bin/`.*

```bash
pip install -e ".[api,dev]"
```
*Installs Tessera itself plus its dependencies, and puts the `tessera-*` commands
on your PATH. The `-e` means "editable" — edit a `.py` file and the installed
command picks the change up immediately, no reinstall.*

*`[api,dev]` adds FastAPI, uvicorn and pytest on top of the core NumPy and
Pillow. Plain `pip install -e .` gets you the carver alone, which is all you
need if you are not running the web UI or the tests.*

```bash
npm install --prefix web
```
*Installs React and Vite for the user interface.*

### 1.1b Check it works before you change anything

```bash
pytest -m "not slow"
```
*A few seconds. Covers the JPEG decoder, the allocation priors, the cluster
view, the erase safety interlocks and the certificate chain. If this is red,
fix that before anything else — everything downstream sits on top of it.*

```bash
pytest
```
*Adds the end-to-end carves of the real disk images, which take a couple of
minutes. These skip themselves if you have not generated `corpus/images/` yet.*

### 1.2 Every time — the full run

```bash
./run.sh
```

That one script rebuilds **everything** from scratch. Nothing is cached, so if
it finishes, the results on your screen are real. Here's what it does, in
order, and what you should see.

**Step 1 — build the test disk images**
```bash
python corpus/generate/synthetic.py
```
Creates two fake hard-disk images with photos deliberately broken into pieces,
plus a *manifest* recording exactly where every piece went (the ground truth).

You should see:
```
easy   -> easy.img  (16 MiB, 4096 clusters)
   EVIDENCE-A.jpg      65593 B  2 fragments  [c120..127, c140..148]  sha=0821fe16f808e59a
   EVIDENCE-B.jpg      63545 B  2 fragments  [c130..136, c151..159]  sha=527f7cc218f16209
hard   -> hard.img  (16 MiB, 4096 clusters)
   EVIDENCE-C.jpg      66851 B  3 fragments  [c109..114, c90..94, c124..129]  sha=11c5b12270e0fefe
```
Look at that last line. `EVIDENCE-C` starts at cluster 109, and its **second**
piece is at cluster **90** — *earlier* on the disk. That backwards jump is the
whole point of the hard test, and it's what defeats every existing tool.

**Step 2 — test the decoder before trusting anything built on it**
```bash
python bench/test_decoder.py
```
Everything we claim depends on our JPEG decoder being correct, so it gets
tested first. It checks that a clean photo decodes fully, that a chopped-off
photo never falsely reports success, and it measures how well we can tell a
correct piece from a foreign one.

You should see it end with:
```
  separation ratio     : 3.4x
ALL DECODER CHECKS PASSED
```
**If this fails, stop.** Every number downstream is meaningless.

**Step 3 — run our carver on both images**
```bash
python carve/carver.py corpus/images/hard.img --out bench/out/ours_hard --json bench/out/ours_hard.json --no-trace
```
You should see:
```
  header c109   OK    3 frag  1900/1900 MCUs  conf=0.99*  7.58s  2477 cand  [c109..114, c90..94, c124..129]
      sha256: 11c5b12270e0fefe...
```
Compare that cluster list and hash to step 1. Identical means we rebuilt the
file **perfectly** — not "looks right", but byte-for-byte identical.

**Step 4 — run the three competitors**
```bash
python bench/run_baselines.py corpus/images/hard.img --json bench/out/baselines_hard.json
```
Runs Foremost, Scalpel, PhotoRec, and PhotoRec **with brute-force mode on**.
That last one matters: PhotoRec has a special mode built specifically for
broken-up JPEGs. We always enable it. Turning it off would make our results
look better and would be dishonest.

*Takes a few minutes — PhotoRec's brute force is slow by design.*

**Step 5 — build the comparison table**
```bash
python bench/report.py
```
Scores everything the same way and prints the table. This is the artefact we
show people.

### 1.3 Running the interface

Two terminals.

```bash
python -m uvicorn api.main:app --port 8781
```
The backend. It serves results and runs live carves.

```bash
npm run dev --prefix web
```
The web interface. Open **http://localhost:5183**.

Ports 8781 and 5183 are deliberately unusual. The normal defaults (8000 and
5173) are what every other Python and React project grabs, and colliding with
an unrelated project is a confusing way to lose an evening.

---

## 2. What every file is for

| path | what it does |
|---|---|
| `run.sh` | Rebuilds everything from scratch. Start here. |
| `CLAUDE.md` | Project memory — competition rules, claims, threats, hard rules. Read before changing anything. |
| `README.md` | The public face: results table and honest limitations. |
| `GUIDE.md` | This file. |
| `corpus/generate/synthetic.py` | Builds the fake disk images and records where every piece went. |
| `corpus/images/` | The generated images and ground truth. Not in version control — regenerate them. |
| `carve/blocks.py` | Reads a disk image as a numbered list of 4 KB chunks; finds photo start-markers. |
| `carve/validators/jpeg.py` | Our JPEG decoder. Answers "does this piece continue the photo, or is it foreign?" |
| `carve/priors/base.py` | Where-pieces-usually-land knowledge. Two versions so we can measure its worth. |
| `carve/beam.py` | The search. Explores many possible orderings at once and keeps the best. |
| `carve/carver.py` | Ties it together; the command-line tool. |
| `bench/test_decoder.py` | Proves the decoder works. Run first, always. |
| `bench/run_baselines.py` | Runs the real competitor tools via Linux. |
| `bench/score.py` | Marks the homework against ground truth. **The only file allowed to compare against the original.** |
| `bench/report.py` | Builds the comparison table and the JSON the interface reads. |
| `bench/diagnose_path.py` | Debugging tool: "why didn't it find the right answer?" |
| `api/main.py` | Backend; streams the live search so the interface can animate it. |
| `web/` | The interface — disk map, live search animation, side-by-side results, table. |
| `model/` | The learned-adjacency experiment (documents/spreadsheets only) |
| `erase/` | Modules 1 & 2 — residue recovery, erasure, validation, certificates |

---

## 3. What actually happens when you hit "Run carve"

This is the story to tell out loud. Learn this section.

**The situation.** Someone deleted photos from a disk, or formatted it. The
disk's index — the table saying "photo.jpg lives at positions 109 to 129" — is
gone. The photo's actual data is still sitting there, but nothing says where it
starts, where it ends, or in what order the pieces go. And the pieces are
*scattered*, with unrelated junk in between. Recovering files in this situation
is called **carving**.

**Step one: find the beginnings.** Every JPEG starts with the same three bytes,
`FF D8 FF`. We read the disk as a list of 4 KB chunks — a chunk is called a
**cluster**, and it's the smallest unit a disk actually hands out to files — and
we look for those three bytes at the start of each. Each hit is a candidate
photo. On `hard.img` we find exactly one, at cluster 109.

**Step two: read the label.** The first part of a JPEG is a header describing
the photo: how big it is, what compression tables were used. From that we
compute something crucial — how many **MCUs** the photo contains. An MCU
("minimum coded unit") is a small tile of the image, usually 16×16 pixels. Our
test photo is 800×600, which works out to exactly **1900** tiles. That number
is a target: a correct reconstruction decodes exactly 1900 tiles and then stops.
Not 1899, not 1901.

**Step three: start decoding and run out of data.** We start decoding tiles.
After about 120 of them we reach the end of cluster 109 and need more data.
Now the real question: **which cluster comes next?**

**Step four: try the obvious answer first.** Almost always the answer is "the
very next one" — disks try hard to keep files in one piece. So we tentatively
glue cluster 110 on and keep decoding. If it decodes cleanly and the image
still looks continuous, we accept it and move on without considering
alternatives. This is called **run merging**, and it's what keeps the program
fast: it collapses a search over thousands of possibilities into a handful of
real decisions.

We sail through 110, 111, 112, 113, 114 this way.

**Step five: the obvious answer stops working.** At cluster 115 the decoder
breaks down. The compressed data stops making sense — technically the decoder
**desyncs** — because cluster 115 isn't part of our photo. We've hit a
**fragment boundary**. Now we have to actually search.

**Step six: gather suspects and interrogate them.** We collect every nearby
cluster that could plausibly be photo data — skipping ones that are all zeros
or obviously text, using a cheap statistical test — and for each one we ask two
questions.

*Question one, pass/fail:* if we resume decoding from exactly the bit we left
off at, and feed in this candidate, does the decoder stay healthy? Candidates
that break it are **eliminated**, not merely marked down. This is a **hard
constraint**, and it's why we never emit the garbage that Foremost and Scalpel
do.

*Question two, the ranking:* here's the subtle part. A surprising number of
foreign candidates *pass* the first test — roughly 90 tiles' worth — because
all JPEGs from the same camera or software share the same compression tables,
so foreign data still decodes as technically valid. Validity alone isn't
enough. So we ask a second question: **does the picture continue?**

Each tile carries a brightness value. In a real photograph, a row of tiles
resembles the row above it — sky above sky, water above water. So we take the
candidate's first row of tiles and compare it to the row above, which lives in
the *previous* fragment, and measure how well they correlate. A genuine
continuation scores 0.69–0.92. A foreign fragment scores below 0.56. **That
comparison across the join — we call it the seam — is where the evidence
lives.**

**Step seven: don't commit.** We don't just take the winner. We keep the best
two dozen possibilities alive simultaneously and carry on with all of them.
This is **beam search**.

Be precise about what it's doing here, because it's tempting to oversell.
On `hard.img` the seam comparison is *decisive at both boundaries* — the true
next cluster wins outright, and comfortably:

| boundary | true next | its score | runner-up |
|---|---|---|---|
| after c114 | **c90** (backwards) | 0.961 | 0.642 |
| after c94 | **c124** | 0.909 | 0.568 |

So on this image beam search is **insurance, not the thing doing the work** —
the picture-continuity evidence alone would have got there. It earns its keep
elsewhere: during development, before the scoring was fixed, the true cluster
on `easy.img` sat *fourth*, and a tool that takes the top answer every time
would have taken the wrong branch and never recovered. The ablation in section 6
shows the same thing from the other side — weaken the search and it loses the
final cluster and never closes the file.

If someone asks "is the beam search necessary?", the honest answer is: *not for
this example, and we can show you the numbers; yes for the general case, and we
can show you those too.*

**Step eight: geography as a tiebreaker.** Between two candidates the picture
likes equally, we lean towards the one in a more plausible place — disks don't
scatter randomly. This is the **allocation prior**. It's deliberately weak:
strong enough to break ties, never strong enough to overrule what the image
itself says. We learned that the hard way (see `CLAUDE.md`, section 6).

**Step nine: the finish line settles it.** Wrong paths die at the end. To be
accepted, a reconstruction must decode **exactly 1900 tiles** and the data must
**end exactly at the JPEG end-marker**. Wrong chains sail past on the tile
count — the decoder resynchronises and happily produces plausible-looking
nonsense — but they don't land on the end-marker. That final check is what
turns "looks about right" into "provably complete".

**The result.** `109, 110, 111, 112, 113, 114 → 90, 91, 92, 93, 94 → 124, 125,
126, 127, 128, 129`. Three fragments, one of them backwards. We hash the
reconstruction and it matches the original exactly.

**What the competitors did with the same disk.** Foremost and Scalpel read
straight from the start marker to the first end marker they found and handed
over an 83 KB file containing our photo's first fragment plus a pile of junk —
the real photo is 66 KB. PhotoRec, including its brute-force mode, returned
nothing at all: its search only ever moves *forwards* from the header, and here
the next piece is behind it.

---

## 4. The five things most likely to break

### 4.1 "No such file: corpus/images/hard.img"

**Why:** the disk images are deliberately not in version control — they're 16 MB
each and fully reproducible. A fresh clone has none.

**Fix:**
```bash
python corpus/generate/synthetic.py
```

### 4.2 Every competitor reports "0 files recovered"

**Why:** WSL Ubuntu isn't installed, or the three carvers aren't. Our harness
reports zero rather than crashing, which looks like a spectacular win. It isn't.

**Recognise it:** *all four* baselines return 0 on *both* images. On a healthy
run Foremost and Scalpel always produce *something*, even if corrupt.

**Fix:** redo section 1.1, then check:
```bash
wsl -d Ubuntu -u root -- bash -c "which photorec foremost scalpel"
```

**Never present a table where the baselines scored zero because they weren't
installed.** That's the most embarrassing possible failure — it looks like
fraud even when it's a mistake.

### 4.3 "progressive JPEG (SOF2) not supported"

**Why:** almost always because you swapped in your own photos. JPEG has two
flavours: *baseline* (decodes top to bottom) and *progressive* (decodes blurry,
then sharpens). We only handle baseline, and we reject progressive **loudly**
rather than silently mis-decoding it.

**Fix:** re-save as baseline. In Python:
```bash
python -c "from PIL import Image; im=Image.open('in.jpg'); im.save('out.jpg', 'JPEG', quality=88, progressive=False)"
```

### 4.4 "Port 5183 is already in use"

**Why:** a previous run is still going, or something else took the port.

**Fix:** find and stop it —
```bash
netstat -ano | grep LISTENING | grep -E ":5183|:8781"
```
then `taskkill //PID <number> //F`. Don't just switch ports; you'll end up with
two copies running and debug a stale one for an hour.

### 4.5 The interface shows an error, or numbers that don't match your terminal

**Why:** the interface only ever reads `bench/out/report.json`. It cannot invent
a number — by design. If that file is missing you get an error; if it's stale
you get last week's results.

**Fix:**
```bash
python bench/report.py
```
That needs `ours_*.json` and `baselines_*.json` to exist first, so if in doubt
just run `./run.sh` again.

---

## 5. Making a test disk image from your own photos

**Be aware this doesn't work yet.** The generator currently paints its own
photographs procedurally — sky, sun, mountains, water — and there's no option to
supply your own. Adding a `--photos <folder>` flag is about a ten-line change to
`corpus/generate/synthetic.py`; ask and it'll be added.

When you do use your own photos, two rules matter, and both come from real
failures:

**They must be baseline JPEGs, not progressive.** See 4.3.

**They must be actual photographs.** Not screenshots, logos, diagrams or flat
graphics. Our whole ranking signal is "does the picture continue smoothly across
the join", and that only exists in images with natural structure. We measured
this: a procedurally-noisy test image gave only **1.4×** separation between
correct and foreign pieces; real photographic content gave **3.4×**. Feed it
screenshots and the carver will get worse, and it won't be a bug.

Sensible test photos: landscapes, faces, street scenes, anything from a phone
camera. Around 800×600 at quality 85–90 gives roughly 17 clusters, which is
enough to fragment interestingly without being slow.

---

## 6. Running the ablation

An **ablation** means removing one part of your system to measure what that part
was actually worth. Without one, "we added a filesystem prior" is just an
assertion.

Normal run, with the prior:
```bash
python carve/carver.py corpus/images/hard.img --prior locality
```

Control run, with the prior switched off:
```bash
python carve/carver.py corpus/images/hard.img --prior uniform
```

**How to read it:**

| prior | outcome | MCUs | candidates | time |
|---|---|---|---|---|
| `locality` | byte-exact | 1900 / 1900 | 8,041 | 73.7 s |
| `uniform` | **never completes the file** | 1868 / 1900 | 158,452 | 1753.9 s |

Read that honestly, because someone will ask. Without the prior, the search
*still* finds the backwards jump — so the prior isn't what solves the hard case;
the image evidence is. What the prior does is keep the search focused enough to
hold on to the final cluster, and it halves the runtime. **That's a real
contribution but a modest one, and overselling it is how you get caught.**

The `uniform` prior is a genuine control: it considers every plausible cluster on
the whole disk in index order, with no notion of nearness, so no geographic
knowledge leaks back in.

---

## 7. Glossary

Terms you'll need to say out loud without hesitating.

**Carving** — recovering files from a disk by recognising their content, when
the disk's index of where files live has been destroyed. The alternative,
easier method — reading that index — is called metadata recovery.

**Cluster** — the smallest unit of disk space a filesystem allocates, typically
4 KB. We work in clusters, not 512-byte sectors, because that's how disks
actually hand out space; using sectors would make our search eight times bigger
for no extra information.

**Sector** — the smallest unit the *hardware* addresses, classically 512 bytes.
A cluster is several sectors.

**LBA (Logical Block Address)** — the position of a sector on the disk,
counting from zero. "A backwards LBA jump" means a file's next piece sits
*earlier* on the disk than the piece before it. Real filesystems do this, and
forward-only tools like PhotoRec can't follow it.

**Fragment** — one contiguous run of a file. A file in three fragments is
stored as three separate stretches with other data in between.

**Out-of-order fragmentation** — when the fragments aren't merely separated but
stored in the wrong sequence on disk. This is `hard.img`, and it's common:
van der Meer et al. found a significant share of fragmented files on 220 real
laptops were out of order.

**Header / footer** — the marker bytes at a file's start and end. For JPEG,
`FF D8 FF` (called **SOI**, start of image) and `FF D9` (**EOI**, end of image).
Naive carvers copy everything between the first header and the next footer,
which is exactly why they emit junk.

**MCU (Minimum Coded Unit)** — the tile a JPEG is compressed in, typically
16×16 pixels. An 800×600 photo has 1900 of them. We count them because the
count is a hard target a correct reconstruction must hit exactly.

**Entropy stream** — the compressed body of a JPEG, after the header. Where the
actual picture data lives.

**Huffman coding** — the compression scheme inside a JPEG. Frequent patterns get
short codes. Important consequence: it **self-synchronises** — feed it wrong
data and after a short confusion it starts producing valid-looking output again.
That's why "it decoded without error" is weak evidence.

**Desync** — when the decoder loses track and starts producing impossible
values. Proof a fragment is foreign. Our strongest pass/fail test.

**DC coefficient** — the average brightness of one tile. Stored as a
*difference* from the previous tile, which is why absolute brightness
comparisons mislead — see `CLAUDE.md` section 6.

**Seam** — the join between the piece we've already accepted and a candidate
next piece. All the useful evidence is concentrated here; comparing whole
fragments mostly measures "is this candidate smooth in itself", which is true of
every photograph.

**Beam search** — a search that keeps the best N partial answers alive at once
instead of committing to the single best at each step. We keep 24. Necessary
because the correct next cluster is sometimes ranked second or third when you
meet it.

**Hard constraint** — a rule that eliminates a candidate outright rather than
lowering its score. Ours: the decoder must not desync, and the file must end at
the end-marker with the exact tile count. Hard constraints are why we never emit
a corrupt file.

**Prior** — knowledge about what's likely *before* looking at the evidence. Ours
is that a file's next cluster is usually nearby, usually just ahead. It breaks
ties; it must never overrule the image evidence.

**Ablation** — deliberately removing a component to measure its contribution.
See section 6.

**Ground truth** — the known-correct answer, recorded when we built the test
image. Used only for marking, never by the carver.

**Byte-exact** — the recovered file is *identical* to the original, verified by
comparing SHA-256 hashes. Not "looks the same" — bit-for-bit identical. This is
our headline metric because it's the only one that can't be argued with.

**SHA-256** — a fingerprint of a file. Change one byte and the fingerprint
changes completely. Two files with the same SHA-256 are the same file.

**SSIM (Structural Similarity Index)** — a measure of how visually similar two
images are, 0 to 1. We use it for partial credit: a competitor's corrupt output
scoring 0.59 tells you it recovered roughly half a recognisable picture. It's a
consolation prize, never the headline.

**False positive** — a "recovered file" that isn't a file at all. EnCase
produced 9,054 of them on one government test. Volume of output is not success.

**Manifest** — our record of exactly which clusters held which file, written
when the test image is built. The answer sheet.

**Validator** — code that checks whether data really is a well-formed file of a
given type. Ours is the JPEG decoder in `carve/validators/jpeg.py`.

**Entropy (Shannon)** — how random a chunk of data looks, 0–8 bits per byte.
Compressed photo data sits near 8, plain text near 4.5, blank space at 0. We use
it as a cheap first filter to skip clusters that obviously aren't photo data.

**TRIM** — a command telling an SSD it may physically erase deleted data. It's
the main reason carving recovers less from modern SSDs, and the standard
objection to this whole project. Our answer is in `CLAUDE.md` section 5.

**Uncalibrated** — our confidence score is a number between 0 and 1 that is
*not yet* a probability. "0.99" does not currently mean "99% of such files are
correct". Always say this. It becomes a real probability in Phase 2.
