"""
Phase 0 corpus generator: hand-laid disk images with exact ground truth.

Two images, deliberately:

  easy.img  Two JPEGs interleaved in file order:
              [filler][A1][B1][A2][B2][filler]
            Header/footer carvers (Foremost, Scalpel) carve from A's SOI to
            the first EOI they meet, swallowing B1 -- corrupted output.

  hard.img  ONE JPEG in three fragments, laid down OUT OF ORDER with a
            backward LBA jump:
              [filler][A2][filler][A1 (SOI)][filler][A3 (EOI)][filler]
            From the header at A1 the next fragment sits at a LOWER cluster
            index. PhotoRec's brute-force mode extends forward from the
            header, so a backward jump is outside its search space.
            Physically realistic: ext4 delayed allocation and wrapped
            log-structured writes both produce backward runs.

Filler is not pure noise -- that would be a strawman. It mixes zeroed
(never-written) clusters, text-like data, and DECOY JPEG body fragments with
no header, which is what unallocated space on a used drive actually looks
like and which gives the beam search something real to reject.

Ground truth (cluster order per file + SHA-256) goes to a manifest. It is
used ONLY by bench/score.py. It is never visible to the carver.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont

CLUSTER = 4096


# ------------------------------------------------------------------ photo ---

def _fbm(h: int, w: int, rnd: np.random.Generator, octaves: int = 6) -> np.ndarray:
    """Fractal (1/f) noise. Gives natural-image spatial statistics."""
    out = np.zeros((h, w), dtype=np.float32)
    amp = 1.0
    total = 0.0
    for o in range(octaves):
        gh = max(2, h >> (octaves - o))
        gw = max(2, w >> (octaves - o))
        grid = rnd.random((gh, gw), dtype=np.float32)
        layer = np.asarray(
            Image.fromarray((grid * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC),
            dtype=np.float32,
        ) / 255.0
        out += layer * amp
        total += amp
        amp *= 0.5
    return out / total


def make_photo(w: int = 800, h: int = 600, seed: int = 1, label: str = "") -> Image.Image:
    """
    A synthetic but photographically-plausible scene.

    Realism matters here for a concrete measurable reason, not aesthetics:
    the carver's discriminator is per-MCU luma DC continuity, and that signal
    only exists if the image has natural spatial statistics. An early version
    of this generator drew random ellipses, and DC continuity separated
    correct from foreign fragments by only 1.4x -- the image was noise, so
    the correct continuation was no smoother than a foreign one. Smooth
    large-scale structure plus 1/f texture is what real photographs have.
    """
    rnd = np.random.default_rng(seed)
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]

    horizon = 0.58 + 0.06 * (seed % 3)

    # --- sky: vertical gradient, deep blue -> warm near the horizon
    t = np.clip(yy / horizon, 0, 1) * np.ones((1, w), dtype=np.float32)
    sky_r = (30 + 205 * t ** 2.2).astype(np.float32)
    sky_g = (60 + 150 * t ** 1.7).astype(np.float32)
    sky_b = (130 + 60 * t ** 0.7).astype(np.float32)

    # --- sun glow
    sx, sy = 0.30 + 0.4 * ((seed * 7) % 10) / 10.0, horizon - 0.10
    d = np.sqrt((xx - sx) ** 2 + ((yy - sy) * (w / h)) ** 2)
    glow = np.exp(-(d ** 2) / 0.020).astype(np.float32)
    disc = np.clip(1.0 - d / 0.045, 0, 1) ** 0.4
    sky_r += 220 * glow + 255 * disc
    sky_g += 150 * glow + 235 * disc
    sky_b += 40 * glow + 180 * disc

    # --- clouds from fractal noise, only in the sky band
    cloud = _fbm(h, w, rnd, 6)
    cband = np.clip((horizon - yy) / horizon, 0, 1) ** 0.6
    cmask = np.clip((cloud - 0.52) * 3.2, 0, 1) * cband
    sky_r = sky_r * (1 - cmask) + (238 + 12 * cloud) * cmask
    sky_g = sky_g * (1 - cmask) + (228 + 12 * cloud) * cmask
    sky_b = sky_b * (1 - cmask) + (225 + 20 * cloud) * cmask

    img = np.stack([sky_r, sky_g, sky_b], axis=-1)

    # --- layered mountain ridges (far = hazy, near = dark)
    for layer in range(3):
        base = horizon - 0.16 + layer * 0.055
        ridge = _fbm(1, w, rnd, 5)[0]
        ridge = np.convolve(ridge, np.ones(max(3, w // 40)) / max(3, w // 40), mode="same")
        prof = base - (0.10 - 0.025 * layer) * (ridge - ridge.mean()) * 4.0
        mask = (yy > prof[None, :]).astype(np.float32)
        shade = 0.62 - 0.18 * layer
        col = np.array([46 + 46 * shade, 58 + 40 * shade, 74 + 34 * shade], dtype=np.float32)
        haze = 0.55 - 0.22 * layer
        img = img * (1 - mask[..., None] * (1 - haze)) + col * mask[..., None] * (1 - haze)

    # --- water below the horizon: reflected sky + horizontal ripples
    wmask = (yy > horizon).astype(np.float32)
    depth = np.clip((yy - horizon) / (1 - horizon), 0, 1)
    refl_idx = np.clip(((horizon - (yy - horizon) * 0.75) * h).astype(np.int32), 0, h - 1)
    refl = img[refl_idx[:, 0], :, :]
    ripple = (np.sin(yy * 190 + _fbm(h, w, rnd, 4) * 9.0) * 0.5 + 0.5).astype(np.float32)
    water = refl * (0.60 + 0.16 * ripple[..., None]) + np.array([8, 20, 34], dtype=np.float32)
    water = water * (1 - 0.30 * depth[..., None])
    img = img * (1 - wmask[..., None]) + water * wmask[..., None]

    # --- global fine grain, like sensor noise
    img += (_fbm(h, w, rnd, 3)[..., None] - 0.5) * 9.0
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")

    if label:
        d2 = ImageDraw.Draw(out)
        try:
            font = ImageFont.truetype("arialbd.ttf", 30)
        except Exception:
            font = ImageFont.load_default()
        d2.rectangle([18, h - 62, 18 + 22 * len(label), h - 20], fill=(8, 10, 16))
        d2.text((30, h - 56), label, fill=(240, 176, 32), font=font)
    return out


def encode_jpeg(img: Image.Image, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    # baseline sequential, no restart markers -- restart markers would make
    # reassembly much easier and would flatter our results.
    img.save(buf, format="JPEG", quality=quality, optimize=False, progressive=False)
    return buf.getvalue()


# ----------------------------------------------------------------- filler ---

class Filler:
    """Plausible unallocated-space content: zeros, text, and decoy JPEG bodies."""

    def __init__(self, seed: int = 42):
        self.rnd = random.Random(seed)
        decoy = encode_jpeg(make_photo(320, 240, seed=seed + 77), quality=70)
        self.decoy_body = decoy[600:]
        words = ("report incident evidence transfer subject vehicle location time "
                 "officer statement recovered device analysis case reference").split()
        self.words = words

    def cluster(self) -> bytes:
        r = self.rnd.random()
        if r < 0.35:
            return b"\x00" * CLUSTER
        if r < 0.62:
            parts = []
            while sum(len(p) for p in parts) < CLUSTER:
                parts.append((" ".join(self.rnd.choice(self.words)
                                       for _ in range(self.rnd.randint(6, 14)))
                              + "\r\n").encode())
            return b"".join(parts)[:CLUSTER]
        if r < 0.85 and len(self.decoy_body) > CLUSTER:
            o = self.rnd.randrange(0, len(self.decoy_body) - CLUSTER)
            return self.decoy_body[o:o + CLUSTER]
        return bytes(self.rnd.getrandbits(8) for _ in range(CLUSTER))

    def run(self, n: int) -> bytes:
        return b"".join(self.cluster() for _ in range(n))


# ------------------------------------------------------------ image layout ---

def split_at_clusters(data: bytes, fracs) -> list:
    """Split `data` into fragments on CLUSTER boundaries at the given fractions."""
    ncl = (len(data) + CLUSTER - 1) // CLUSTER
    cuts = sorted({max(1, min(ncl - 1, int(round(ncl * f)))) for f in fracs})
    pieces, prev = [], 0
    for c in cuts:
        pieces.append(data[prev * CLUSTER : c * CLUSTER])
        prev = c
    pieces.append(data[prev * CLUSTER :])
    return [p for p in pieces if p]


class DiskBuilder:
    def __init__(self, total_clusters: int, filler: Filler):
        self.total = total_clusters
        self.filler = filler
        self.buf = bytearray()
        self.placed = []            # (name, frag_index, start_cluster, n_clusters)

    @property
    def cursor(self) -> int:
        return len(self.buf) // CLUSTER

    def pad_to(self, cluster: int) -> None:
        gap = cluster - self.cursor
        if gap < 0:
            raise ValueError("cannot pad backwards (cursor=%d, target=%d)"
                             % (self.cursor, cluster))
        if gap:
            self.buf += self.filler.run(gap)

    def place(self, name: str, frag_index: int, data: bytes, at: int) -> None:
        self.pad_to(at)
        n = (len(data) + CLUSTER - 1) // CLUSTER
        padded = data + b"\x00" * (n * CLUSTER - len(data))
        self.buf += padded
        self.placed.append((name, frag_index, at, n))

    def finish(self) -> bytes:
        self.pad_to(self.total)
        return bytes(self.buf)


def build(kind: str, out_dir: str, size_mib: int = 16, seed: int = 1) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    total_clusters = size_mib * 1024 * 1024 // CLUSTER
    filler = Filler(seed=seed + 500)
    b = DiskBuilder(total_clusters, filler)

    files = {}

    if kind == "easy":
        a = encode_jpeg(make_photo(800, 600, seed=seed, label="EVIDENCE-A"))
        c = encode_jpeg(make_photo(800, 600, seed=seed + 4, label="EVIDENCE-B"))
        a1, a2 = split_at_clusters(a, [0.45])
        c1, c2 = split_at_clusters(c, [0.45])
        files = {"EVIDENCE-A.jpg": a, "EVIDENCE-B.jpg": c}
        # interleaved, but each file's fragments are in increasing order
        b.place("EVIDENCE-A.jpg", 0, a1, at=120)
        b.place("EVIDENCE-B.jpg", 0, c1, at=b.cursor + 2)
        b.place("EVIDENCE-A.jpg", 1, a2, at=b.cursor + 3)
        b.place("EVIDENCE-B.jpg", 1, c2, at=b.cursor + 2)

    elif kind == "hard":
        a = encode_jpeg(make_photo(800, 600, seed=seed + 11, label="EVIDENCE-C"))
        a1, a2, a3 = split_at_clusters(a, [0.34, 0.67])
        files = {"EVIDENCE-C.jpg": a}
        # fragment 2 lands BEFORE fragment 1 -> backward jump from the header
        b.place("EVIDENCE-C.jpg", 1, a2, at=90)
        b.place("EVIDENCE-C.jpg", 0, a1, at=b.cursor + 14)
        b.place("EVIDENCE-C.jpg", 2, a3, at=b.cursor + 9)

    else:
        raise ValueError("unknown image kind: %s" % kind)

    raw = b.finish()
    img_path = os.path.join(out_dir, "%s.img" % kind)
    with open(img_path, "wb") as f:
        f.write(raw)

    # ---- manifest: ground truth, for bench/score.py ONLY -----------------
    by_file = {}
    for name, idx, start, n in b.placed:
        by_file.setdefault(name, []).append({"fragment": idx, "start_cluster": start,
                                             "n_clusters": n})
    manifest = {
        "image": os.path.basename(img_path),
        "kind": kind,
        "cluster_size": CLUSTER,
        "total_clusters": total_clusters,
        "size_bytes": len(raw),
        "files": [],
    }
    for name, data in files.items():
        frags = sorted(by_file[name], key=lambda d: d["fragment"])
        order = []
        for fr in frags:
            order.extend(range(fr["start_cluster"], fr["start_cluster"] + fr["n_clusters"]))
        manifest["files"].append({
            "name": name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "n_fragments": len(frags),
            "fragments": frags,
            "cluster_order": order,
        })
        with open(os.path.join(out_dir, "truth_" + name), "wb") as f:
            f.write(data)

    mpath = os.path.join(out_dir, "%s.manifest.json" % kind)
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Phase 0 disk images")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "images"))
    ap.add_argument("--size-mib", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kinds", default="easy,hard")
    args = ap.parse_args()

    for kind in args.kinds.split(","):
        m = build(kind.strip(), args.out, args.size_mib, args.seed)
        print("%-6s -> %s  (%d MiB, %d clusters)"
              % (kind, m["image"], m["size_bytes"] // 1048576, m["total_clusters"]))
        for f in m["files"]:
            spans = ", ".join("c%d..%d" % (fr["start_cluster"],
                                           fr["start_cluster"] + fr["n_clusters"] - 1)
                              for fr in f["fragments"])
            print("   %-18s %6d B  %d fragments  [%s]  sha=%s"
                  % (f["name"], f["size_bytes"], f["n_fragments"], spans,
                     f["sha256"][:16]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
