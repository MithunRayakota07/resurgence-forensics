"""
Extract the parts of the NIST CFReDS 'File Carving Graphic Files' (2023)
download that we actually use.

The archive is 892 MB and covers six formats; we support baseline JPEG only,
so by default this pulls the three JPG layouts plus every source file and the
documentation. Pass --all to extract everything.
"""

from __future__ import annotations

import argparse
import os
import zipfile

ROOT = "file-carving-graphic-files-cftt-test-data/"
DOCS = ("README.TXT", "graphic file carving test data draft 1.pdf")


def wanted(name: str, fmt: str, take_all: bool) -> bool:
    if name.startswith("__MACOSX") or name.endswith("/"):
        return False
    rel = name[len(ROOT):] if name.startswith(ROOT) else name
    if rel in DOCS:
        return True
    if rel.startswith("source/"):
        return True                       # sources are small and are our ground truth
    if rel.startswith("images/"):
        return take_all or ("-%s.dd" % fmt) in rel
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "cfreds_raw", "cftt-test-data.zip"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "cfreds"))
    ap.add_argument("--format", default="jpg")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    z = zipfile.ZipFile(args.zip)
    n = 0
    for name in z.namelist():
        if not wanted(name, args.format, args.all):
            continue
        rel = name[len(ROOT):] if name.startswith(ROOT) else name
        dest = os.path.join(args.out, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with z.open(name) as a, open(dest, "wb") as b:
            b.write(a.read())
        print("%-56s %11d" % (rel, os.path.getsize(dest)))
        n += 1
    print("extracted %d entries to %s" % (n, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
