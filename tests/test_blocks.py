"""
The cluster-addressed view of a disk image, and the cheap pre-filters.

The header scan and the entropy filter decide what the expensive decoder ever
sees, so a bug here silently shrinks the search space and the carver just
"fails to find" files with no error anywhere.
"""

from __future__ import annotations

import os

import pytest

from carve.blocks import DEFAULT_CLUSTER, SOI, ClusterView, cluster_span


@pytest.fixture
def disk(tmp_path):
    """
    A 12-cluster image: zeros, then a planted JPEG header, then high-entropy
    data, then text.
    """
    cs = DEFAULT_CLUSTER
    parts = {
        "zeros": [0, 1],
        "header": [2],
        "random": [3, 4, 5, 6],
        "text": [7, 8],
        "more_zeros": [9, 10, 11],
    }
    data = bytearray(12 * cs)
    data[2 * cs:2 * cs + 3] = SOI
    rnd = os.urandom(4 * cs)
    data[3 * cs:7 * cs] = rnd
    data[7 * cs:9 * cs] = (b"the quick brown fox jumps over the lazy dog. " * 400)[:2 * cs]

    path = tmp_path / "disk.img"
    path.write_bytes(bytes(data))
    return ClusterView(str(path)), parts


def test_cluster_count(disk):
    view, _ = disk
    assert len(view) == 12
    assert view.n_clusters == 12


def test_cluster_returns_exactly_one_cluster(disk):
    view, _ = disk
    assert len(view.cluster(0)) == DEFAULT_CLUSTER


def test_concat_joins_clusters_in_the_order_given(disk):
    view, _ = disk
    joined = view.concat([5, 3, 4])
    assert joined == view.cluster(5) + view.cluster(3) + view.cluster(4)
    assert len(joined) == 3 * DEFAULT_CLUSTER


def test_concat_of_nothing_is_empty(disk):
    view, _ = disk
    assert view.concat([]) == b""


# --------------------------------------------------------------------------
# header scan
# --------------------------------------------------------------------------

def test_finds_the_planted_header(disk):
    view, parts = disk
    assert view.find_jpeg_headers() == parts["header"]


def test_header_scan_only_looks_at_cluster_boundaries(tmp_path):
    """
    File data starts on a cluster boundary. Scanning every byte offset -- what
    naive carvers do -- mostly turns up EXIF thumbnails embedded inside other
    JPEGs, which are not files to recover.
    """
    cs = DEFAULT_CLUSTER
    data = bytearray(3 * cs)
    data[cs + 100:cs + 103] = SOI          # mid-cluster: a thumbnail, not a file
    path = tmp_path / "thumb.img"
    path.write_bytes(bytes(data))
    assert ClusterView(str(path)).find_jpeg_headers() == []


# --------------------------------------------------------------------------
# entropy pre-filter
# --------------------------------------------------------------------------

def test_entropy_ranks_zeros_below_text_below_random(disk):
    view, parts = disk
    e = view.entropies()
    zeros = e[parts["zeros"][0]]
    text = e[parts["text"][0]]
    random_data = e[parts["random"][0]]
    assert zeros == pytest.approx(0.0, abs=1e-6)
    assert zeros < text < random_data
    assert random_data > 7.9


def test_entropy_returns_one_value_per_cluster(disk):
    view, _ = disk
    assert view.entropies().shape == (len(view),)


def test_high_entropy_filter_keeps_compressed_and_drops_zeros(disk):
    view, parts = disk
    keep = set(view.high_entropy_clusters(threshold=7.0).tolist())
    assert set(parts["random"]) <= keep
    assert keep.isdisjoint(parts["zeros"])
    assert keep.isdisjoint(parts["text"])


def test_entropy_is_cached(disk):
    view, _ = disk
    assert view.entropies() is view.entropies()


# --------------------------------------------------------------------------
# cluster_span
# --------------------------------------------------------------------------

@pytest.mark.parametrize("n_bytes,expected", [
    (0, 0),
    (1, 1),
    (DEFAULT_CLUSTER, 1),
    (DEFAULT_CLUSTER + 1, 2),
    (3 * DEFAULT_CLUSTER, 3),
])
def test_cluster_span_rounds_up(n_bytes, expected):
    assert cluster_span(n_bytes) == expected
