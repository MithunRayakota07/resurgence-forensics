"""
Shared fixtures.

Everything here builds its own inputs. No test may depend on `corpus/`, which
is gitignored and 4.9 GB -- a clean clone must be able to run the suite.
The one exception is the end-to-end carve, which skips itself when the
generated disk images are absent.
"""

from __future__ import annotations

import os

import pytest

from carve.validators import jpeg as J
from corpus.generate.synthetic import encode_jpeg, make_photo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def photo_jpeg() -> bytes:
    """
    A real photographic JPEG.

    Deliberately not procedural noise: CLAUDE.md section 6 records that noise
    gives only 1.4x correct-vs-foreign separation where photographic statistics
    give 3.4x, so a suite built on noise would pass while the carver rotted.
    """
    return encode_jpeg(make_photo(800, 600, seed=1), quality=88)


@pytest.fixture(scope="session")
def other_jpeg() -> bytes:
    """A DIFFERENT photo, same encoder -- so it shares the standard Huffman
    tables and decodes as valid symbols. This is the foreign competitor the
    whole carving problem is about."""
    return encode_jpeg(make_photo(800, 600, seed=99), quality=88)


@pytest.fixture(scope="session")
def photo_header(photo_jpeg):
    return J.parse_header(photo_jpeg, 0)


def images_dir() -> str:
    return os.path.join(REPO_ROOT, "corpus", "images")


def require_image(name: str) -> str:
    """Skip rather than fail when the generated corpus is not present."""
    path = os.path.join(images_dir(), name)
    if not os.path.exists(path):
        pytest.skip("%s not generated; run `tessera-gen-corpus` to enable" % name)
    return path
