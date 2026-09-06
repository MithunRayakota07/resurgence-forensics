"""
Cluster-level view of a raw disk image.

Carving happens at CLUSTER granularity (4096 B), not 512 B sectors. Real
filesystems allocate in clusters, so a 512 B unit multiplies the search
graph by 8x for exactly zero extra information -- and the FFT-75 literature's
habit of working at 512 B is an artefact of the classification framing, not
of how disks behave.
"""

from __future__ import annotations

import math
import os

import numpy as np

DEFAULT_CLUSTER = 4096
SOI = b"\xff\xd8\xff"


class ClusterView:
    """Read-only cluster-addressed view over a disk image file."""

    def __init__(self, path: str, cluster_size: int = DEFAULT_CLUSTER):
        self.path = path
        self.cluster_size = cluster_size
        self.size = os.path.getsize(path)
        self.n_clusters = self.size // cluster_size
        with open(path, "rb") as f:
            self._data = f.read()
        self._entropy_cache = None

    def __len__(self) -> int:
        return self.n_clusters

    def cluster(self, i: int) -> bytes:
        o = i * self.cluster_size
        return self._data[o : o + self.cluster_size]

    def concat(self, clusters) -> bytes:
        cs = self.cluster_size
        return b"".join(self._data[c * cs : (c + 1) * cs] for c in clusters)

    # ------------------------------------------------------------ entropy --
    def entropies(self) -> np.ndarray:
        """
        Per-cluster Shannon entropy in bits/byte.

        Used only as a cheap pre-filter: entropy-coded JPEG data sits at
        ~7.9-8.0, zeroed clusters at 0, text at ~4.5. It removes the obvious
        non-candidates before the expensive decoder runs. It deliberately
        does NOT remove random filler or decoy JPEG bodies -- those are the
        hard cases and must reach the validator.
        """
        if self._entropy_cache is not None:
            return self._entropy_cache
        cs = self.cluster_size
        n = self.n_clusters
        arr = np.frombuffer(self._data[: n * cs], dtype=np.uint8).reshape(n, cs)
        out = np.zeros(n, dtype=np.float32)
        for i in range(n):
            counts = np.bincount(arr[i], minlength=256)
            p = counts[counts > 0] / cs
            out[i] = float(-(p * np.log2(p)).sum())
        self._entropy_cache = out
        return out

    def high_entropy_clusters(self, threshold: float = 7.0) -> np.ndarray:
        return np.nonzero(self.entropies() >= threshold)[0]

    # ------------------------------------------------------------- headers --
    def find_jpeg_headers(self) -> list:
        """
        Cluster indices whose first bytes are a JPEG SOI.

        File data starts on a cluster boundary, so we only check offset 0.
        Scanning every byte offset (what naive carvers do) mostly finds
        embedded thumbnails inside other JPEGs.
        """
        return [i for i in range(self.n_clusters)
                if self._data[i * self.cluster_size : i * self.cluster_size + 3] == SOI]


def cluster_span(n_bytes: int, cluster_size: int = DEFAULT_CLUSTER) -> int:
    return int(math.ceil(n_bytes / cluster_size))
